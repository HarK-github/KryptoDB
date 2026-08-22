"""
Unified KryptoDB Daemon server.
"""
import kryptodb
from fastapi import FastAPI, HTTPException, APIRouter, BackgroundTasks, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List
import logging
import os
import uvicorn
from pathlib import Path

from kryptodb.daemon.keyring import keyring
from kryptodb.integrity.partition_crypto import Encryptor
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client
from kryptodb.storage.bootstrap import bootstrap_storage
from kryptodb.daemon.auth import AuthContext, api_keys, parse_bearer
from kryptodb.daemon.sessions import SessionState, sessions
from kryptodb.integrity.knowledge_migration import migrate_legacy_fact
from kryptodb.integrity.bootstrap_state import is_enabled as bootstrap_enabled, lock as lock_bootstrap, status as bootstrap_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Lifecycle & Health ---
def startup_event():
    logger.info("Starting KryptoDB Daemon...")
    try:
        bootstrap_storage()
        logger.info("Storage connected and bootstrapped.")
    except Exception as e:
        logger.error(f"Failed to connect to storage: {e}")
        raise

def shutdown_event():
    logger.info("Shutting down KryptoDB Daemon...")
    neo4j_client.close()
    redis_client.close()

def check_health() -> dict:
    neo4j_up = False
    try:
        neo4j_up = neo4j_client.check_health()
    except Exception:
        pass
    redis_up = False
    try:
        redis_up = redis_client.check_health()
    except Exception:
        pass
    return {
        "status": "up" if neo4j_up and redis_up else "down",
        "neo4j": "up" if neo4j_up else "down",
        "redis": "up" if redis_up else "down"
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_event()
    yield
    shutdown_event()

# --- API Routes ---
router = APIRouter()
encryptor = Encryptor()


def require_auth(authorization: str | None = Header(default=None)) -> AuthContext:
    context = api_keys.authenticate(parse_bearer(authorization))
    if context is None:
        raise HTTPException(status_code=401, detail="Valid bearer API key required")
    return context


def require_admin(context: AuthContext = Depends(require_auth)) -> AuthContext:
    if not context.is_admin:
        raise HTTPException(status_code=403, detail="Administrator API key required")
    return context


def get_request_session(
    context: AuthContext,
    session_id: str | None,
) -> SessionState | None:
    if context.is_admin:
        return None
    session = sessions.get(session_id)
    if not session or session.agent_id != context.agent_id or session.key_id != context.key_id:
        raise HTTPException(status_code=403, detail="Valid session activation required")
    return session

class SessionStartRequest(BaseModel):
    active_partitions: List[str]

from kryptodb.daemon.events import event_bus

@router.get("/audit")
def get_audit_log(context: AuthContext = Depends(require_auth)):
    return event_bus.audit_log

@router.get("/agents")
def get_agents(context: AuthContext = Depends(require_admin)):
    import json
    try:
        with open("data/agents.json", "r") as f:
            return json.load(f)
    except Exception:
        return {}

@router.get("/ontology")
def get_ontology(context: AuthContext = Depends(require_admin)):
    try:
        with open("data/ontology.toml", "r") as f:
            return {"content": f.read()}
    except Exception:
        return {"content": ""}

@router.post("/session")
def start_session(req: SessionStartRequest, context: AuthContext = Depends(require_auth)):
    # Preserve direct internal callers while making the HTTP route authenticated.
    if not isinstance(context, AuthContext):
        keyring.clear()
        for p in req.active_partitions:
            keyring.add_key(p, encryptor._get_or_create_key(p))
        return {"active_partitions": req.active_partitions, "status": "active"}

    requested = set(req.active_partitions)
    allowed_scopes = requested if context.is_admin else api_keys.effective_scopes(context)
    if not requested.issubset(allowed_scopes):
        raise HTTPException(status_code=403, detail="Requested partition is outside API-key scope")
    session = sessions.create(context.agent_id, context.key_id, allowed_scopes)
    try:
        for partition in requested:
            if bootstrap_enabled() and partition not in encryptor.scope_keys:
                session.keys[partition] = b""
            else:
                session.keys[partition] = encryptor._get_or_create_key(partition)
    except RuntimeError as exc:
        sessions.revoke_for_key(context.key_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "session_id": session.session_id,
        "active_partitions": sorted(session.keys),
        "allowed_scopes": sorted(allowed_scopes),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        "status": "active",
    }


def _legacy_start_session(req: SessionStartRequest):
    keyring.clear()
    
    from kryptodb.memory_api.session_search import session_index
    session_index.clear()
    
    for p in req.active_partitions:
        key = encryptor._get_or_create_key(p)
        keyring.add_key(p, key)
        
    # Stream decrypted names into Bloom filter
    from kryptodb.storage.neo4j_client import neo4j_client
    if neo4j_client.driver:
        query = """
        MATCH (s)-[r:FACT]->(o)
        WHERE r.permission_scope IN $scopes AND r.valid_to IS NULL
        RETURN s, r, o
        """
        records, _, _ = neo4j_client.execute_query_with_retry(query, scopes=req.active_partitions)
        for record in records:
            s_node = record["s"]
            o_node = record["o"]
            r_edge = record["r"]
            scope = r_edge["permission_scope"]
            
            s_name = encryptor.decrypt(scope, s_node.get("name_ct")) if s_node.get("name_ct") else s_node.get("id")
            o_name = encryptor.decrypt(scope, o_node.get("name_ct")) if o_node.get("name_ct") else o_node.get("id")
            
            edge_dict = {
                "edge_id": r_edge["id"],
                "subject": s_name,
                "predicate": r_edge["predicate"],
                "object": o_name,
                "permission_scope": scope
            }
            session_index.add_edge(edge_dict)
            
    return {"active_partitions": req.active_partitions, "status": "active"}

@router.get("/session")
def get_session(context: AuthContext = Depends(require_auth), x_kryptodb_session: str | None = Header(default=None)):
    if isinstance(context, AuthContext):
        session = get_request_session(context, x_kryptodb_session)
        if session is None:
            return {"active_partitions": [], "status": "admin"}
        return {
            "session_id": session.session_id,
            "active_partitions": sorted(session.keys),
            "allowed_scopes": sorted(session.scopes),
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        }
    return {"active_partitions": list(keyring._keys.keys())}

@router.get("/memory/{agent_id}")
def get_memory(agent_id: str, background_tasks: BackgroundTasks, query: str = None, requested_scope: str = None, subject_id: str = None, mode: str = "local", context: AuthContext = Depends(require_auth), x_kryptodb_session: str | None = Header(default=None)):
    from kryptodb.memory_api.agents import get_agent
    from kryptodb.graph.read_engine import ReadEngine
    if isinstance(context, AuthContext):
        if not context.is_admin and context.agent_id != agent_id:
            raise HTTPException(status_code=403, detail="Agent identity is bound to the API key")
        session = get_request_session(context, x_kryptodb_session)
        active_scopes = set(session.keys) if session else set()
        if context.is_admin:
            target = get_agent(agent_id)
            agent = target if target else {"id": "admin", "allowed_scopes": list(keyring._keys.keys())}
        else:
            agent = get_agent(context.agent_id)
            if not agent:
                raise HTTPException(status_code=403, detail="Unknown agent")
    else:
        session = None
        active_scopes = set(keyring._keys)
        agent = get_agent(agent_id) or {"id": agent_id, "allowed_scopes": list(keyring._keys.keys())}
    if not agent:
        raise HTTPException(status_code=403, detail="Unknown agent")

    allowed_agent_scopes = set(agent.get("allowed_scopes", []))
    if "*" in allowed_agent_scopes or (isinstance(context, AuthContext) and context.is_admin and not allowed_agent_scopes):
        allowed_agent_scopes = set(keyring._keys.keys())

    if requested_scope and requested_scope.lower() not in ("all", "*", "global"):
        req_scopes = [s.strip() for s in requested_scope.split(",") if s.strip()]
        for s in req_scopes:
            if s not in allowed_agent_scopes and not (isinstance(context, AuthContext) and context.is_admin):
                return {"status": "denied_permission", "facts": []}
            if s not in active_scopes and not (isinstance(context, AuthContext) and context.is_admin):
                return {"status": "denied_inactive", "facts": []}
        scopes_to_query = req_scopes
    else:
        if isinstance(context, AuthContext) and context.is_admin:
            scopes_to_query = sorted(allowed_agent_scopes or set(keyring._keys.keys()))
        else:
            scopes_to_query = sorted(allowed_agent_scopes & (active_scopes or allowed_agent_scopes))

    if mode == "global":
        from kryptodb.graph.gds_engine import GDSEngine
        gds = GDSEngine()
        communities = gds.get_communities(scopes_to_query)
        if not communities:
            # Trigger detection if communities are empty
            for scope in scopes_to_query:
                gds.run_louvain_community_detection(scope)
            communities = gds.get_communities(scopes_to_query)
            
        return {"status": "ok", "mode": "global", "communities": communities}

    if isinstance(context, AuthContext) and context.is_admin:
        for scope in scopes_to_query:
            if scope not in keyring._keys:
                try:
                    keyring.add_key(scope, encryptor._get_or_create_key(scope))
                except Exception as e:
                    logger.debug(f"Scope {scope} key not available: {e}")
            if session and scope not in session._keys:
                k = keyring.get_key(scope)
                if k:
                    session.add_key(scope, k)
        effective_scopes = [s for s in scopes_to_query if s in keyring._keys]
    else:
        effective_scopes = list(set(scopes_to_query) & (active_scopes or set(keyring._keys.keys())))
    if not effective_scopes:
        return {"status": "denied_inactive", "facts": []}

    read_engine = ReadEngine(
        session_keyring=session,
        session_search_index=session.search_index if session else None,
    )
    facts = read_engine.retrieve_active_facts(effective_scopes, subject_id=subject_id, query_str=query)
    
    if not facts:
        return {"status": "not_found", "facts": []}
        
    return {"status": "ok", "mode": "local", "facts": facts}


class MemoryQueryRequest(BaseModel):
    query: str | None = None
    subject_id: str | None = None
    requested_scope: str | None = None
    mode: str = "local"


@router.post("/memory/{agent_id}/query")
def post_memory_query(
    agent_id: str,
    req: MemoryQueryRequest,
    background_tasks: BackgroundTasks,
    context: AuthContext = Depends(require_auth),
    x_kryptodb_session: str | None = Header(default=None),
):
    """Body-based MCP query path; avoids putting search terms in URLs/logs."""
    return get_memory(
        agent_id=agent_id,
        background_tasks=background_tasks,
        query=req.query,
        requested_scope=req.requested_scope,
        subject_id=req.subject_id,
        mode=req.mode,
        context=context,
        x_kryptodb_session=x_kryptodb_session,
    )

@router.get("/graph/snapshot")
def get_graph_snapshot(agent_id: str, context: AuthContext = Depends(require_auth), x_kryptodb_session: str | None = Header(default=None)):
    from kryptodb.memory_api.agents import get_agent
    
    from kryptodb.storage.neo4j_client import neo4j_client

    if isinstance(context, AuthContext) and not context.is_admin and context.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Agent identity is bound to the API key")
    
    if not neo4j_client.driver:
        return {"nodes": [], "edges": []}
        
    agent = get_agent(agent_id)
    if not agent:
        if agent_id == "admin" or (isinstance(context, AuthContext) and context.is_admin):
            query = "MATCH (n) RETURN DISTINCT n.permission_scope AS scope UNION MATCH ()-[r]->() RETURN DISTINCT r.permission_scope AS scope"
            scopes_res, _, _ = neo4j_client.execute_query_with_retry(query)
            allowed_scopes = {r["scope"] for r in scopes_res if r.get("scope")}
        else:
            raise HTTPException(status_code=403, detail="Unknown agent")
    else:
        allowed_scopes = set(agent.get("allowed_scopes", []))
    
    if "*" in allowed_scopes:
        query = "MATCH (n) RETURN DISTINCT n.permission_scope AS scope UNION MATCH ()-[r]->() RETURN DISTINCT r.permission_scope AS scope"
        scopes_res, _, _ = neo4j_client.execute_query_with_retry(query)
        allowed_scopes = {r["scope"] for r in scopes_res if r.get("scope")}
    
    active_partitions = set(keyring._keys.keys())
    if isinstance(context, AuthContext) and not context.is_admin:
        session = get_request_session(context, x_kryptodb_session)
        active_partitions = set(session.keys)
    if agent_id == "admin" or (isinstance(context, AuthContext) and context.is_admin):
        cleared_scopes = list(allowed_scopes)
    else:
        cleared_scopes = list(allowed_scopes & active_partitions)
    
    if not cleared_scopes:
        return {"nodes": [], "edges": []}
        
    # Get nodes belonging to cleared scopes
    nodes_q = """
    MATCH (n)
    WHERE n.permission_scope IN $scopes
    OPTIONAL MATCH (n)-[rel:FACT]-()
    WITH n, collect(rel) AS rels
    RETURN n.id AS id,
           labels(n)[0] AS type,
           n.permission_scope AS scope,
           n.name_ct IS NOT NULL OR any(rel IN rels WHERE rel.knowledge_ct IS NOT NULL) AS is_encrypted,
           any(rel IN rels WHERE rel.encryption_status = 'pending' OR (rel IS NOT NULL AND rel.knowledge_ct IS NULL)) AS is_pending
    """
    nodes_res, _, _ = neo4j_client.execute_query_with_retry(nodes_q, scopes=cleared_scopes)

    nodes = [dict(r) for r in nodes_res]
    
    # Get active edges belonging to cleared scopes
    edges_q = """
    MATCH (s)-[r]->(o) 
    WHERE r.permission_scope IN $scopes AND r.valid_to IS NULL
    RETURN s.id AS source, type(r) AS predicate, o.id AS target, r.id AS id, r.permission_scope AS scope, r.trust_weight AS trust_weight
    """
    edges_res, _, _ = neo4j_client.execute_query_with_retry(edges_q, scopes=cleared_scopes)
    edges = [dict(r) for r in edges_res]

    return {"nodes": nodes, "edges": edges}

from kryptodb.graph.schema import Edge
class ProposeFactRequest(BaseModel):
    agent_id: str
    edge: dict

from fastapi import BackgroundTasks
import uuid

def run_cascade_task(task_id: str, revoked_edge_id: str, valid_to_iso: str, superseded_by_id: str):
    from kryptodb.storage.redis_client import redis_client
    redis_client.client.set(f"task:{task_id}", "running", ex=3600)
    
    from kryptodb.graph.write_engine import WriteEngine
    try:
        engine = WriteEngine()
        engine._cascade_revocation(revoked_edge_id, valid_to_iso, superseded_by_id)
        redis_client.client.set(f"task:{task_id}", "completed", ex=3600)
    except Exception as e:
        redis_client.client.set(f"task:{task_id}", f"failed: {str(e)}", ex=3600)

@router.get("/status/{task_id}")
def get_task_status(task_id: str, context: AuthContext = Depends(require_auth)):
    from kryptodb.storage.redis_client import redis_client
    status = redis_client.client.get(f"task:{task_id}")
    if not status:
        return {"task_id": task_id, "status": "not_found"}
    if isinstance(status, bytes):
        status = status.decode('utf-8')
    return {"task_id": task_id, "status": status}

@router.post("/memory")
def post_memory(req: ProposeFactRequest, background_tasks: BackgroundTasks, context: AuthContext = Depends(require_auth)):
    from kryptodb.memory_api.propose import propose_fact
    from kryptodb.memory_api.conflict import detect_conflict
    
    edge = Edge(**req.edge)

    if isinstance(context, AuthContext):
        if not context.is_admin and req.agent_id != context.agent_id:
            raise HTTPException(status_code=403, detail="Agent identity is bound to the API key")
        if not context.is_admin and edge.permission_scope not in api_keys.effective_scopes(context):
            raise HTTPException(status_code=403, detail="Fact scope is outside API-key scope")
        proposer_id = req.agent_id or context.agent_id
    else:
        proposer_id = req.agent_id
    
    # Prompt injection content-flagging heuristic (Phase 8.1)
    suspicious_patterns = [
        "ignore previous", "system prompt", "bypass", "you are now",
        "<script>", "sql injection", "drop table"
    ]
    
    content_to_check = f"{edge.subject_id} {edge.predicate} {edge.object_id}".lower()
    if any(pattern in content_to_check for pattern in suspicious_patterns):
        edge.is_suspicious = True
        logger.warning(f"Flagged suspicious content in proposed edge {edge.id}")

    try:
        existing_edges = detect_conflict(edge.subject_id, edge.predicate, edge.object_id, edge.permission_scope)
        status, committed_edge, cascade_args = propose_fact(proposer_id, edge, False, existing_edges)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    
    response = {"status": status}
    
    if cascade_args:
        task_id = str(uuid.uuid4())
        background_tasks.add_task(
            run_cascade_task, 
            task_id, 
            cascade_args["revoked_edge_id"], 
            cascade_args["valid_to_iso"], 
            cascade_args["superseded_by_id"]
        )
        response["task_id"] = task_id
        
    return response

class AgentChatRequest(BaseModel):
    agent_id: str = "agent-1"
    user_scope: str = "research"
    message: str
    thread_id: str | None = None

class AgentApproveRequest(BaseModel):
    thread_id: str
    approved: bool
try:
    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt
except ImportError:
    Command = None
    class GraphInterrupt(Exception):
        pass

_agent_checkpointer = None

def get_agent_checkpointer():
    global _agent_checkpointer
    if _agent_checkpointer is None:
        try:
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver
            import os
            
            db_path = os.environ.get(
                "KRYPTODB_CHECKPOINT_DB",
                "/data/checkpointer.sqlite" if os.path.isdir("/data") else "checkpointer.sqlite",
            )
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
            _agent_checkpointer = SqliteSaver(conn)
            _agent_checkpointer.setup()
        except Exception as e:
            logger.warning(f"Could not initialize SqliteSaver: {e}")
            try:
                from langgraph.checkpoint.memory import MemorySaver
                _agent_checkpointer = MemorySaver()
                logger.info("Fell back to MemorySaver for checkpointer")
            except Exception as inner_e:
                logger.error(f"Could not initialize MemorySaver: {inner_e}")
                return None
    return _agent_checkpointer

@router.post("/agent/chat")
async def agent_chat(req: AgentChatRequest, context: AuthContext = Depends(require_auth)):
    """Invoke or continue conversation with the KryptoDB Agent Graph."""
    import asyncio
    from kryptodb.agent.graph import get_compiled_graph
    from langchain_core.messages import HumanMessage

    thread_id = req.thread_id or f"session_{uuid.uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}

    llm = get_active_llm()
    graph = get_compiled_graph(llm=llm, checkpointer=get_agent_checkpointer())
    
    input_state = {
        "messages": [HumanMessage(content=req.message)],
        "agent_id": req.agent_id,
        "user_scope": req.user_scope,
    }

    try:
        res = await asyncio.to_thread(graph.invoke, input_state, config=config)
        state_snapshot = await asyncio.to_thread(graph.get_state, config)
        
        pending_interrupts = []
        if state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        pending_interrupts.append(intr.value if hasattr(intr, "value") else str(intr))

        from langchain_core.messages import AIMessage
        last_msg = ""
        if res.get("messages"):
            for m in reversed(res["messages"]):
                if isinstance(m, AIMessage) or (isinstance(m, dict) and m.get("role") == "assistant"):
                    last_msg = m.content if hasattr(m, "content") else (m.get("content") if isinstance(m, dict) else str(m))
                    break

        return {
            "thread_id": thread_id,
            "status": res.get("status", "completed"),
            "agent_response": last_msg or res.get("reasoning_output"),
            "reasoning": res.get("reasoning_output"),
            "recalled_facts": res.get("recalled_facts", []),
            "denied_recalls": res.get("denied_recalls", []),
            "pending_facts": res.get("pending_facts", []),
            "pending_interrupts": pending_interrupts,
        }
    except GraphInterrupt:
        state_snapshot = await asyncio.to_thread(graph.get_state, config)
        pending_interrupts = []
        if state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        pending_interrupts.append(intr.value if hasattr(intr, "value") else str(intr))

        return {
            "thread_id": thread_id,
            "status": "pending_approval",
            "agent_response": "Pending Human Approval: This memory write involves a sensitive scope and requires human authorization.",
            "pending_interrupts": pending_interrupts,
            "pending_facts": state_snapshot.values.get("pending_facts", [])
        }
    except Exception as e:
        logger.error(f"Error in agent_chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/agent/approve")
async def agent_approve(req: AgentApproveRequest, context: AuthContext = Depends(require_auth)):
    """Resume agent execution following HITL write approval or rejection."""
    import asyncio
    from kryptodb.agent.graph import get_compiled_graph
    
    config = {"configurable": {"thread_id": req.thread_id}}
    graph = get_compiled_graph(checkpointer=get_agent_checkpointer())
    
    try:
        if Command is None:
            raise HTTPException(status_code=500, detail="LangGraph is not available")
        res = await asyncio.to_thread(graph.invoke, Command(resume={"approved": req.approved}), config=config)
        
        last_msg = ""
        if res.get("messages"):
            m = res["messages"][-1]
            last_msg = m.content if hasattr(m, "content") else str(m)

        return {
            "thread_id": req.thread_id,
            "status": res.get("status", "completed"),
            "agent_response": last_msg or res.get("reasoning_output"),
            "approved": req.approved,
            "pending_facts": res.get("pending_facts", [])
        }
    except GraphInterrupt:
        state_snapshot = await asyncio.to_thread(graph.get_state, config)
        pending_interrupts = []
        if state_snapshot.tasks:
            for task in state_snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        pending_interrupts.append(intr.value if hasattr(intr, "value") else str(intr))

        return {
            "thread_id": req.thread_id,
            "status": "pending_approval",
            "agent_response": "Pending Human Approval: This memory write involves a sensitive scope and requires human authorization.",
            "pending_interrupts": pending_interrupts,
            "pending_facts": state_snapshot.values.get("pending_facts", [])
        }
    except Exception as e:
        logger.error(f"Error in agent_approve: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Phase 2.5 — Chat Transport Layer Endpoints ────────────────────────────

@router.get("/chat/{thread_id}/history")
def get_chat_history(thread_id: str, context: AuthContext = Depends(require_auth)):
    """Retrieve checkpointed conversation history for a given thread_id."""
    from kryptodb.agent.graph import get_compiled_graph
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_compiled_graph(checkpointer=get_agent_checkpointer())
    
    state_snapshot = graph.get_state(config)
    messages = []
    if state_snapshot.values and "messages" in state_snapshot.values:
        for m in state_snapshot.values["messages"]:
            role = "user" if type(m).__name__ == "HumanMessage" else "agent"
            content = m.content if hasattr(m, "content") else str(m)
            messages.append({"role": role, "content": content})
            
    return {
        "thread_id": thread_id,
        "messages": messages,
        "status": state_snapshot.values.get("status", "idle") if state_snapshot.values else "idle",
        "pending_facts": state_snapshot.values.get("pending_facts", []) if state_snapshot.values else []
    }

@router.post("/chat/{thread_id}/message")
async def chat_message(thread_id: str, req: AgentChatRequest, context: AuthContext = Depends(require_auth)):
    """Send user message to thread_id."""
    req.thread_id = thread_id
    return await agent_chat(req, context)

@router.post("/chat/{thread_id}/approve")
async def chat_approve(thread_id: str, context: AuthContext = Depends(require_auth)):
    """Approve pending fact write on thread_id."""
    req = AgentApproveRequest(thread_id=thread_id, approved=True)
    return await agent_approve(req, context)

@router.post("/chat/{thread_id}/reject")
async def chat_reject(thread_id: str, context: AuthContext = Depends(require_auth)):
    """Reject pending fact write on thread_id."""
    req = AgentApproveRequest(thread_id=thread_id, approved=False)
    return await agent_approve(req, context)

@router.websocket("/chat/{thread_id}/stream")
async def chat_stream_websocket(websocket: WebSocket, thread_id: str):
    """Live event stream WebSocket emitting agent_token, recall_event, denial_event, pending_approval."""
    await websocket.accept()
    from kryptodb.agent.graph import get_compiled_graph
    config = {"configurable": {"thread_id": thread_id}}

    llm = get_active_llm()
    graph = get_compiled_graph(llm=llm, checkpointer=get_agent_checkpointer())

    try:
        while True:
            data = await websocket.receive_json()
            user_msg = data.get("message", "")
            agent_id = data.get("agent_id", "agent-1")
            user_scope = data.get("user_scope", "research")

            from langchain_core.messages import HumanMessage
            input_state = {
                "messages": [HumanMessage(content=user_msg)],
                "agent_id": agent_id,
                "user_scope": user_scope
            }

            try:
                import queue
                import threading
                import asyncio
                
                stream_q = queue.Queue()
                def run_stream():
                    try:
                        for mode, payload in graph.stream(input_state, config, stream_mode=["messages", "updates"]):
                            stream_q.put(("data", mode, payload))
                        stream_q.put(("done", None, None))
                    except Exception as e:
                        stream_q.put(("error", e, None))
                
                t = threading.Thread(target=run_stream)
                t.start()
                
                while True:
                    try:
                        msg_type, mode, payload = await asyncio.to_thread(stream_q.get, True, 0.1)
                    except queue.Empty:
                        continue
                        
                    if msg_type == "done":
                        break
                    if msg_type == "error":
                        raise mode
                        
                    if mode == "messages":
                        token, metadata = payload
                        msg_class = type(token).__name__
                        if msg_class not in ("AIMessageChunk", "AIMessage"):
                            continue
                        message_node = metadata.get("langgraph_node") if isinstance(metadata, dict) else None
                        if message_node not in ("reasoning_node", "denied_handling"):
                            continue
                        await websocket.send_json({
                            "type": "agent_token", 
                            "content": token.content if hasattr(token, "content") else str(token),
                            "is_chunk": msg_class == "AIMessageChunk"
                        })
                    elif mode == "updates":
                        for node_name, state_update in payload.items():
                            if node_name != "tools":
                                await websocket.send_json({"type": "agent_status", "status": f"Executing step: {node_name}..."})
                            if isinstance(state_update, dict):
                                _msgs = state_update.get("messages") or []
                                if not isinstance(_msgs, (list, tuple)):
                                    _msgs = [_msgs]
                                for _m in _msgs:
                                    if hasattr(_m, "tool_calls") and _m.tool_calls:
                                        import json
                                        for tc in _m.tool_calls:
                                            args_str = json.dumps(tc.get("args", {}))
                                            await websocket.send_json({"type": "agent_status", "status": f"Calling tool `{tc.get('name')}` with args: {args_str}"})
                                if node_name in ("reasoning_node", "denied_handling", "direct_write_node"):
                                    update_messages = state_update.get("messages") or []
                                    if not isinstance(update_messages, (list, tuple)):
                                        update_messages = [update_messages]
                                    for update_message in reversed(update_messages):
                                        content = getattr(update_message, "content", None)
                                        if content is None and isinstance(update_message, dict):
                                            content = update_message.get("content")
                                        if isinstance(content, list):
                                            content = "".join(
                                                block.get("text", "") if isinstance(block, dict) else str(block)
                                                for block in content
                                            )
                                        if content:
                                            await websocket.send_json({
                                                "type": "agent_token",
                                                "content": str(content),
                                                "is_chunk": False,
                                            })
                                            break
                                if state_update.get("denied_recalls"):
                                    await websocket.send_json({
                                        "type": "denial_event",
                                        "scope": state_update["denied_recalls"][0].get("scope", user_scope),
                                        "reason": state_update["denied_recalls"][0].get("status", "no_key")
                                    })
                                elif state_update.get("recalled_facts"):
                                    await websocket.send_json({
                                        "type": "recall_event",
                                        "count": len(state_update["recalled_facts"]),
                                        "scope": state_update["recalled_facts"][0].get("scope", user_scope)
                                    })
                                if state_update.get("pending_facts"):
                                    await websocket.send_json({
                                        "type": "pending_approval",
                                        "fact": state_update["pending_facts"][0]
                                    })
                await websocket.send_json({"type": "done"})
            except GraphInterrupt:
                await websocket.send_json({
                    "type": "pending_approval",
                    "status": "pending_approval",
                    "reason": "high_sensitivity_write_approval_required"
                })
            except Exception as exc:
                logger.exception("Chat stream failed for thread %s", thread_id)
                await websocket.send_json({
                    "type": "error",
                    "message": "The agent could not complete this request.",
                    "detail": str(exc),
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error in chat_stream: {e}")

# ── Phase 2.6 — LLM Model Selection Endpoints ─────────────────────────────────

ACTIVE_LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:0.5b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

def get_active_llm():
    global ACTIVE_LLM_MODEL
    if ACTIVE_LLM_MODEL == "deterministic-engine":
        return None

    if ACTIVE_LLM_MODEL.startswith("gpt-"):
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=ACTIVE_LLM_MODEL)
        except Exception as e:
            logger.warning(f"Failed to instantiate OpenAI model '{ACTIVE_LLM_MODEL}': {e}")
            return None

    installed_models = []
    try:
        import urllib.request, json
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", headers={'User-Agent': 'KryptoDB'})
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                installed_models = [m["name"] for m in data.get("models", []) if "embed" not in m["name"]]
    except Exception as err:
        logger.warning(f"Could not query Ollama tags: {err}")

    target_model = ACTIVE_LLM_MODEL
    if installed_models and target_model not in installed_models:
        matched = [m for m in installed_models if target_model in m or m.startswith(target_model)]
        if matched:
            target_model = matched[0]
        else:
            tool_capable = [m for m in installed_models if "qwen" in m or "llama" in m or "mistral" in m]
            target_model = tool_capable[0] if tool_capable else installed_models[0]
        logger.info(f"Requested model '{ACTIVE_LLM_MODEL}' not pulled in Ollama. Auto-selected '{target_model}'.")
        ACTIVE_LLM_MODEL = target_model

    try:
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            from langchain_community.chat_models import ChatOllama
        return ChatOllama(model=target_model, base_url=OLLAMA_BASE_URL)
    except Exception as e:
        logger.warning(f"Failed to instantiate ChatOllama model '{target_model}': {e}")
        return None

class ModelConfigChange(BaseModel):
    model: str

@router.get("/models")
def list_available_models():
    """Returns active model, Ollama URL, and detected/available LLM models."""
    global ACTIVE_LLM_MODEL
    import urllib.request
    import json
    
    ollama_models = []
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", headers={'User-Agent': 'KryptoDB'})
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                ollama_models = [m["name"] for m in data.get("models", [])]
    except Exception:
        pass

    defaults = ["llama3.2", "llama3.1", "mistral", "qwen2.5", "gpt-4o-mini", "deterministic-engine"]
    all_models = list(dict.fromkeys(ollama_models + defaults))

    return {
        "active_model": ACTIVE_LLM_MODEL,
        "ollama_url": OLLAMA_BASE_URL,
        "available_models": all_models
    }

@router.post("/config/model")
def set_active_model(cfg: ModelConfigChange):
    """Dynamically switch the active LLM model."""
    global ACTIVE_LLM_MODEL
    ACTIVE_LLM_MODEL = cfg.model.strip()
    logger.info(f"Active LLM model set to: {ACTIVE_LLM_MODEL}")
    return {"status": "success", "active_model": ACTIVE_LLM_MODEL}

class DedupRequest(BaseModel):
    agent_id: str

def run_knn_task(task_id: str, scopes: list[str]):
    from kryptodb.storage.redis_client import redis_client
    redis_client.client.set(f"task:{task_id}", "running", ex=3600)
    
    from kryptodb.graph.gds_engine import GDSEngine
    try:
        gds = GDSEngine()
        for scope in scopes:
            gds.run_knn_similarity(scope)
        redis_client.client.set(f"task:{task_id}", "completed", ex=3600)
    except Exception as e:
        redis_client.client.set(f"task:{task_id}", f"failed: {str(e)}", ex=3600)

@router.post("/dedup")
def trigger_knn_dedup(req: DedupRequest, background_tasks: BackgroundTasks):
    from kryptodb.memory_api.agents import get_agent
    agent = get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=403, detail="Unknown agent")
        
    task_id = str(uuid.uuid4())
    background_tasks.add_task(run_knn_task, task_id, agent["allowed_scopes"])
    
    return {"status": "accepted", "task_id": task_id}

class OnboardRequest(BaseModel):
    agent_id: str
    sample_facts: list[str]
    trust_prior: float = 0.5

@router.post("/onboard")
def onboard_agent(req: OnboardRequest, context: AuthContext = Depends(require_admin)):
    from kryptodb.memory_api.onboarding import auto_onboard_agent
    res = auto_onboard_agent(req.agent_id, req.sample_facts, req.trust_prior)
    return res

class ReclassifyRequest(BaseModel):
    node_id: str | None = None
    knowledge_id: str | None = None
    to_scope: str


class LegacyMigrationRequest(BaseModel):
    scopes: List[str]
    lock: bool = False


@router.get("/maintenance/migration-preflight")
def migration_preflight(context: AuthContext = Depends(require_auth)):
    if not isinstance(context, AuthContext) or not context.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if not neo4j_client.driver:
        raise HTTPException(status_code=503, detail="Neo4j not connected")
        
    query = """
    MATCH (s)-[r:FACT]->(o)
    RETURN
        r.encryption_status AS status,
        r.permission_scope AS scope,
        r.knowledge_ct IS NOT NULL AS is_encrypted,
        r.pending_envelope IS NULL AS missing_envelope,
        r.subject_id IS NULL AS missing_subject_id,
        r.object_id IS NULL AS missing_object_id,
        s.name_ct IS NULL AS missing_subject_ct,
        o.name_ct IS NULL AS missing_object_ct,
        count(r) AS c
    """
    records, _, _ = neo4j_client.execute_query_with_retry(query)
    
    counts = {}
    total_pending = 0
    total_encrypted = 0
    invalid_records = 0
    
    for record in records:
        scope = record["scope"]
        c = record["c"]
        if record["status"] == "pending" or (not record["is_encrypted"]):
            if scope not in counts: counts[scope] = 0
            counts[scope] += c
            total_pending += c
            recoverable_legacy = (
                not record["missing_subject_id"] and not record["missing_object_id"]
            ) or (
                not record["missing_subject_ct"] and not record["missing_object_ct"]
            )
            if record["missing_envelope"] and not recoverable_legacy:
                invalid_records += c
        elif record["is_encrypted"]:
            total_encrypted += c

    missing_keys = []
    for scope in counts:
        if scope not in encryptor.scope_keys:
            missing_keys.append(scope)

    return {
        "status": "ok",
        "bootstrap": bootstrap_status(),
        "pending_counts": counts,
        "total_pending": total_pending,
        "total_encrypted": total_encrypted,
        "invalid_records": invalid_records,
        "missing_keys": missing_keys
    }


@router.post("/maintenance/migrate-legacy")
def migrate_legacy_records(
    req: LegacyMigrationRequest,
    context: AuthContext = Depends(require_auth),
    x_kryptodb_session: str | None = Header(default=None),
):
    """Convert legacy facts without exposing their plaintext to the caller."""
    if not isinstance(context, AuthContext):
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if context.is_admin:
        session = None
        requested = set(req.scopes)
        # Admins can migrate any scope that has a loaded backend key
        if not requested.issubset(set(encryptor.scope_keys.keys())):
            raise HTTPException(status_code=403, detail="Every migration scope must have a configured server key")
    else:
        session = get_request_session(context, x_kryptodb_session)
        requested = set(req.scopes)
        if not requested.issubset(set(session.keys)):
            raise HTTPException(status_code=403, detail="Every migration scope must be active in the session")

    if not neo4j_client.driver:
        raise HTTPException(status_code=503, detail="Neo4j not connected")

    query = """
    MATCH (s)-[r:FACT]->(o)
    WHERE r.permission_scope IN $scopes
      AND r.knowledge_ct IS NULL
    RETURN r.id AS edge_id, r.permission_scope AS scope,
           properties(r) AS edge_props,
           properties(s) AS subject_props,
           properties(o) AS object_props
    """
    records, _, _ = neo4j_client.execute_query_with_retry(query, scopes=sorted(requested))
    migrated = 0
    failed = 0
    for record in records:
        try:
            replacement = migrate_legacy_fact(
                encryptor=encryptor,
                partition=record["scope"],
                edge_id=record["edge_id"],
                edge_props=dict(record["edge_props"]),
                subject_props=dict(record["subject_props"]),
                object_props=dict(record["object_props"]),
                session_keyring=session,
            )
            replacement.pop("legacy_fields_to_remove", None)
            update_query = """
            MATCH ()-[r:FACT {id: $edge_id}]->()
            SET r += $replacement
            REMOVE r.predicate, r.source_agent_id, r.pending_envelope, r.encryption_status
            """
            neo4j_client.execute_query_with_retry(
                update_query,
                edge_id=record["edge_id"],
                replacement=replacement,
            )
            migrated += 1
        except Exception:
            failed += 1

    remaining = len(records) - migrated
    bootstrap_locked = False
    if req.lock:
        if failed or remaining:
            raise HTTPException(status_code=409, detail="Cannot lock bootstrap mode while migration has failures or pending records")
        lock_bootstrap()
        bootstrap_locked = True

    return {
        "status": "ok" if failed == 0 else "partial",
        "scopes": sorted(requested),
        "examined": len(records),
        "migrated": migrated,
        "failed": failed,
        "remaining": remaining,
        "bootstrap_locked": bootstrap_locked,
    }

@router.post("/reclassify")
def reclassify_node(req: ReclassifyRequest, context: AuthContext = Depends(require_auth), x_kryptodb_session: str | None = Header(default=None)):
    session = None
    if isinstance(context, AuthContext) and not context.is_admin:
        session = get_request_session(context, x_kryptodb_session)
        if req.to_scope not in session.scopes:
            raise HTTPException(status_code=403, detail="Target scope is outside API-key scope")
    if req.knowledge_id:
        if not context.is_admin and (session is None or req.to_scope not in session.keys):
            raise HTTPException(status_code=403, detail="Old and new scopes must be active in the session")
        if not neo4j_client.driver:
            raise HTTPException(status_code=500, detail="Neo4j not connected")

        fact_query = """
        MATCH ()-[r:FACT {id: $knowledge_id}]->()
        RETURN properties(r) AS props
        """
        fact_res, _, _ = neo4j_client.execute_query_with_retry(fact_query, knowledge_id=req.knowledge_id)
        if not fact_res:
            raise HTTPException(status_code=404, detail="Knowledge item not found")
        old_props = dict(fact_res[0]["props"])
        old_scope = old_props.get("permission_scope")
        if old_scope not in session.keys:
            raise HTTPException(status_code=403, detail="Old scope must be active in the session")
        decrypt_keyring = session
        if context.is_admin:
            from kryptodb.daemon.keyring import SessionKeyring
            decrypt_keyring = SessionKeyring()
            decrypt_keyring.add_key(old_scope, encryptor._get_or_create_key(old_scope))
        envelope = encryptor.decrypt_knowledge(
            old_scope,
            req.knowledge_id,
            old_props,
            decrypt_keyring,
        )
        if envelope is None:
            raise HTTPException(status_code=403, detail="Knowledge cannot be decrypted with the active session")

        replacement = encryptor.encrypt_knowledge(req.to_scope, req.knowledge_id, envelope)
        update_query = """
        MATCH ()-[r:FACT {id: $knowledge_id}]->()
        SET r += $replacement,
            r.permission_scope = $to_scope
        REMOVE r.predicate, r.source_agent_id
        """
        neo4j_client.execute_query_with_retry(
            update_query,
            knowledge_id=req.knowledge_id,
            replacement=replacement,
            to_scope=req.to_scope,
        )
        return {
            "status": "ok",
            "knowledge_id": req.knowledge_id,
            "from_scope": old_scope,
            "to_scope": req.to_scope,
        }

    if not req.node_id:
        raise HTTPException(status_code=400, detail="node_id or knowledge_id is required")
    if not neo4j_client.driver:
        raise HTTPException(status_code=500, detail="Neo4j not connected")
        
    # 1. Fetch the node
    query = "MATCH (n:Entity {id: $node_id}) RETURN n.permission_scope AS old_scope, n.name_ct AS name_ct, n.name AS plaintext_name"
    res, _, _ = neo4j_client.execute_query_with_retry(query, node_id=req.node_id)
    if not res:
        raise HTTPException(status_code=404, detail="Node not found")
        
    old_scope = res[0]["old_scope"]
    name_ct = res[0]["name_ct"]
    plaintext_name = res[0].get("plaintext_name")
    
    # 2. Decrypt with old partition key (requires active session)
    if name_ct:
        decrypt_keyring = session
        if context.is_admin:
            from kryptodb.daemon.keyring import SessionKeyring
            decrypt_keyring = SessionKeyring()
            decrypt_keyring.add_key(old_scope, encryptor._get_or_create_key(old_scope))
        plaintext_name = encryptor.decrypt(old_scope, name_ct, decrypt_keyring)
        if not plaintext_name:
            raise HTTPException(status_code=403, detail="Cannot decrypt node. Ensure old partition is active in session.")
    elif not plaintext_name:
        # Fallback to node ID for structural nodes that need forced encryption
        plaintext_name = req.node_id
        
    # 3. Re-encrypt with new partition key
    new_ct = encryptor.encrypt(req.to_scope, plaintext_name)
    new_blind_idx = encryptor.compute_blind_idx(req.to_scope, plaintext_name)
    
    # 4. Update node in Neo4j
    update_q = """
    MATCH (n:Entity {id: $node_id})
    SET n.permission_scope = $to_scope,
        n.name_ct = $new_ct,
        n.name_blind_idx = $new_blind_idx,
        n.is_encrypted = true
    REMOVE n.name
    """
    neo4j_client.execute_query_with_retry(
        update_q, 
        node_id=req.node_id, 
        to_scope=req.to_scope,
        new_ct=new_ct,
        new_blind_idx=new_blind_idx
    )
    
    return {"status": "ok", "message": f"Node {req.node_id} reclassified from {old_scope} to {req.to_scope}"}

from fastapi import WebSocket, WebSocketDisconnect
from kryptodb.daemon.events import event_bus, GraphEvent
import asyncio

@router.websocket("/ws/graph-events")
async def websocket_graph_events(websocket: WebSocket, agent_id: str, api_key: str | None = None):
    # Key resolution priority:
    # 1. Authorization header (native/server clients)
    # 2. api_key query param (browser clients — subprotocols reject arbitrary chars)
    # 3. kryptodb-bearer.<key> subprotocol (legacy; kept for compatibility)
    bearer = parse_bearer(websocket.headers.get("authorization"))
    requested_protocols = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    protocol_key = next((p[len("kryptodb-bearer."):] for p in requested_protocols if p.startswith("kryptodb-bearer.")), None)
    resolved_key = bearer or api_key or protocol_key
    context = api_keys.authenticate(resolved_key)
    if context is None:
        await websocket.close(code=1008, reason="Valid bearer API key required")
        return
    if not context.is_admin and context.agent_id != agent_id:
        await websocket.close(code=1008, reason="Agent identity is bound to the API key")
        return
    # Only echo back the subprotocol when one was actually requested
    await websocket.accept(subprotocol=f"kryptodb-bearer.{protocol_key}" if protocol_key else None)

    from kryptodb.memory_api.agents import get_agent
    
    
    agent = get_agent(context.agent_id) if not context.is_admin else None
    if not agent and agent_id != "admin":
        await websocket.close(code=1008, reason="Unknown agent")
        return
        
    client_id = str(uuid.uuid4())
    queue = await event_bus.connect(client_id)
    try:
        while True:
            event: GraphEvent = await queue.get()
            if agent:
                allowed_scopes = set(agent.get("allowed_scopes", [])) & api_keys.effective_scopes(context)
            else:
                # "admin" has all scopes implicitly
                allowed_scopes = {event.permission_scope}

            session = None if context.is_admin else sessions.get(websocket.headers.get("x-kryptodb-session"))
            active_partitions = set(session.keys) if session else set()
            # The admin graph viewer is already authorized for every scope and
            # does not have an agent session/partition keyring to activate.
            # Keep the event payload intact so the UI can animate traversals.
            if context.is_admin:
                await websocket.send_json(event.model_dump(mode='json'))
                continue
            cleared_scopes = allowed_scopes & active_partitions
            
            if event.permission_scope in cleared_scopes:
                # Fully cleared, send the real event
                await websocket.send_json(event.model_dump(mode='json'))
            elif event.permission_scope in allowed_scopes:
                # Allowed but locked -> send opaque access_denied
                opaque_event = GraphEvent(
                    event_type="access_denied",
                    permission_scope=event.permission_scope,
                    timestamp=event.timestamp
                )
                await websocket.send_json(opaque_event.model_dump(mode='json'))
            else:
                # Not authorized at all -> silently drop
                pass
    except WebSocketDisconnect:
        event_bus.disconnect(client_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        event_bus.disconnect(client_id)

# --- App Definition ---
app = FastAPI(title="KryptoDB Daemon", version="0.1.0", lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_endpoint():
    status = check_health()
    if status["status"] == "down":
        raise HTTPException(status_code=503, detail=status)
    return status

@app.get("/admin/graph")
def admin_graph(context: AuthContext = Depends(require_admin)):
    """Admin endpoint for the TUI to view graph structure."""
    if not neo4j_client.driver:
        return {"nodes": [], "edges": []}
    
    # Get all nodes
    nodes_res, _, _ = neo4j_client.execute_query_with_retry("MATCH (n) RETURN n.id AS id, labels(n)[0] AS type, n.permission_scope AS scope, n.community_id AS community_id")
    nodes = [dict(r) for r in nodes_res]
    
    # Get all active edges
    edges_res, _, _ = neo4j_client.execute_query_with_retry("MATCH (s)-[r]->(o) WHERE r.valid_to IS NULL RETURN s.id AS source, type(r) AS predicate, o.id AS target, r.permission_scope AS scope")
    edges = [dict(r) for r in edges_res]
    
    return {"nodes": nodes, "edges": edges}

@app.get("/admin/scopes")
def admin_scopes(context: AuthContext = Depends(require_admin)):
    """Admin endpoint to get all distinct permission scopes currently in the graph."""
    if not neo4j_client.driver:
        return {"scopes": []}
    
    query = """
    MATCH (n) RETURN DISTINCT n.permission_scope AS scope
    UNION
    MATCH ()-[r]->() RETURN DISTINCT r.permission_scope AS scope
    """
    res, _, _ = neo4j_client.execute_query_with_retry(query)
    scopes = {r["scope"] for r in res if r["scope"]}
    return {"scopes": sorted(list(scopes))}

@app.get("/admin/scope-keys")
def scope_key_status(context: AuthContext = Depends(require_admin)):
    """Return non-secret customer scope-key metadata for the admin UI."""
    statuses = {item["scope"]: item for item in encryptor.scope_key_status()}
    try:
        import json
        with open("data/agents.json", "r") as f:
            configured_agents = json.load(f).values()
        for agent in configured_agents:
            for scope in agent.get("allowed_scopes", []):
                statuses.setdefault(scope, {
                    "scope": scope,
                    "key_id": None,
                    "configured": False,
                    "fingerprint": None,
                })
    except Exception:
        pass
        
    if neo4j_client.driver:
        query = """
        MATCH (n) WHERE n.permission_scope IS NOT NULL RETURN DISTINCT n.permission_scope AS scope
        UNION
        MATCH ()-[r]-() WHERE r.permission_scope IS NOT NULL RETURN DISTINCT r.permission_scope AS scope
        """
        records, _, _ = neo4j_client.execute_query_with_retry(query)
        for r in records:
            if r["scope"]:
                statuses.setdefault(r["scope"], {
                    "scope": r["scope"],
                    "key_id": None,
                    "configured": False,
                    "fingerprint": None,
                })
                
    return {"keys": [statuses[scope] for scope in sorted(statuses)]}

class ConfigureScopeKeyRequest(BaseModel):
    scope: str
    key_id: str
    key_base64: str

@app.post("/admin/scope-keys/configure")
def configure_scope_key(req: ConfigureScopeKeyRequest, context: AuthContext = Depends(require_admin)):
    import os, json, base64
    from fastapi import HTTPException
    
    try:
        key_bytes = base64.b64decode(req.key_base64)
        if len(key_bytes) != 32:
            raise ValueError("Key must be 32 bytes")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 key or incorrect length (must be 32 bytes for AES-256-GCM).")
        
    keys_file = os.environ.get("KRYPTODB_SCOPE_KEYS_FILE", "kryptodb-scope-keys.json")
    
    current_keys = {}
    if os.path.exists(keys_file):
        try:
            with open(keys_file, "r") as f:
                current_keys = json.load(f)
        except Exception:
            pass
            
    current_keys[req.scope] = {
        "key_id": req.key_id,
        "key": req.key_base64
    }
    
    try:
        with open(keys_file, "w") as f:
            json.dump(current_keys, f, indent=2)
    except Exception:
        pass # Ignore permission errors for Docker bind mounts
        
    # Update in memory
    encryptor.scope_keys[req.scope] = (req.key_id, key_bytes)
    
    return {"status": "ok", "scope": req.scope}

class UpdateAgentRequest(BaseModel):
    agent_id: str
    scopes: List[str]
@router.get("/admin/scopes")
def list_all_scopes(context: AuthContext = Depends(require_admin)):
    if not neo4j_client.driver:
        raise HTTPException(status_code=503, detail="Neo4j not connected")
        
    query = """
    MATCH (n) WHERE n.permission_scope IS NOT NULL RETURN DISTINCT n.permission_scope AS scope
    UNION
    MATCH ()-[r]-() WHERE r.permission_scope IS NOT NULL RETURN DISTINCT r.permission_scope AS scope
    """
    records, _, _ = neo4j_client.execute_query_with_retry(query)
    scopes = sorted(list(set(r["scope"] for r in records if r["scope"])))
    return {"scopes": scopes}



class DatasetPrepareRequest(BaseModel):
    dataset_id: str
    scope: str
    mode: str = "new"


@app.post("/admin/datasets/prepare")
def prepare_dataset(req: DatasetPrepareRequest, context: AuthContext = Depends(require_admin)):
    dataset_id = req.dataset_id.strip()
    scope = req.scope.strip()
    if not dataset_id or not scope:
        raise HTTPException(status_code=400, detail="dataset_id and scope are required")
    if req.mode not in {"new", "existing"}:
        raise HTTPException(status_code=400, detail="mode must be new or existing")
    configured = scope in encryptor.scope_keys
    if not configured and not bootstrap_enabled():
        raise HTTPException(status_code=409, detail=f"Customer scope key is not configured for: {scope}")
    record = {
        "dataset_id": dataset_id,
        "scope": scope,
        "mode": req.mode,
        "key_id": encryptor.scope_keys[scope][0] if configured else None,
        "status": "ready" if configured else "pending_key",
    }
    import json
    redis_client.client.set(f"dataset:{dataset_id}", json.dumps(record))
    return record

@app.post("/admin/agents")
def update_agent(req: UpdateAgentRequest, context: AuthContext = Depends(require_admin)):
    import os
    import json
    
    data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    json_path = os.path.join(data_dir, "agents.json")
    
    agents = {}
    if os.path.exists(json_path):
        with open(json_path, "r") as f:
            try:
                agents = json.load(f)
            except Exception:
                pass
                
    # Update or create agent entry
    if req.agent_id not in agents:
        agents[req.agent_id] = {"id": req.agent_id, "trust_prior": 0.5, "allowed_scopes": []}
        
    agents[req.agent_id]["allowed_scopes"] = req.scopes
    
    with open(json_path, "w") as f:
        json.dump(agents, f, indent=2)

    sessions.revoke_for_agent(req.agent_id)
        
    return {"status": "ok", "agent_id": req.agent_id, "scopes": req.scopes}


class CreateApiKeyRequest(BaseModel):
    agent_id: str
    scopes: List[str]
    key_id: str | None = None


@app.post("/admin/api-keys")
def create_api_key(req: CreateApiKeyRequest, context: AuthContext = Depends(require_admin)):
    from kryptodb.memory_api.agents import get_agent
    agent = get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Unknown agent")
    requested = set(req.scopes)
    if not requested.issubset(set(agent.get("allowed_scopes", []))):
        raise HTTPException(status_code=400, detail="Key scopes exceed agent allowed scopes")
    # Reject scopes whose customer encryption key is not configured.
    # In production (customer_keys_required=True) every scope must have a key.
    # In development, warn but allow unconfigured scopes.
    missing_keys = [s for s in sorted(requested) if s not in encryptor.scope_keys]
    if missing_keys:
        if encryptor.customer_keys_required and not bootstrap_enabled():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot create API key: customer encryption key not configured "
                    f"for scope(s): {missing_keys}. "
                    "Configure KRYPTODB_SCOPE_KEYS_JSON / KRYPTODB_SCOPE_KEYS_FILE before provisioning."
                ),
            )
        else:
            logger.warning(
                "Creating API key for scope(s) without a customer key (development mode only): %s",
                missing_keys,
            )
    raw_key, record = api_keys.create_key(req.agent_id, sorted(requested), req.key_id)
    return {
        "status": "created",
        "api_key": raw_key,
        **record,
        "warning": "Store this key now; it will not be shown again.",
    }



@app.post("/admin/api-keys/{key_id}/revoke")
def revoke_api_key(key_id: str, context: AuthContext = Depends(require_admin)):
    changed = api_keys.revoke(key_id)
    sessions.revoke_for_key(key_id)
    if not changed:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "revoked", "key_id": key_id}


@app.get("/admin/api-keys")
def list_api_keys(context: AuthContext = Depends(require_admin)):
    return {"keys": api_keys.list_keys()}

app.include_router(router)

# ── Optional: serve built Next.js frontend from same origin ──────────────────
# When running inside the Docker image the standalone build is copied to
# /app/ui-server. Mount it so the browser hits http://localhost:8000/ directly.
_UI_SERVER_DIR = Path(__file__).parent.parent.parent / "ui-server"

if _UI_SERVER_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles
    # Static assets (JS, CSS, images)
    _static = _UI_SERVER_DIR / ".next" / "static"
    if _static.is_dir():
        app.mount("/_next/static", StaticFiles(directory=str(_static)), name="next-static")
    _public = _UI_SERVER_DIR / "public"
    if _public.is_dir():
        app.mount("/public", StaticFiles(directory=str(_public)), name="public")

    # Fallback: serve Next.js index for any unmatched route (SPA behaviour)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        # API and WS routes are matched before this wildcard
        index = _UI_SERVER_DIR / "server" / "app" / "index.html"
        if not index.exists():
            index = _UI_SERVER_DIR / "server" / "pages" / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return FileResponse(str(_UI_SERVER_DIR / "public" / "index.html"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port)
