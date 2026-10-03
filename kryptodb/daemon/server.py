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

from kryptodb.integrity.partition_crypto import Encryptor
from kryptodb.storage import neo4j_client, redis_client, bootstrap_storage
from kryptodb.daemon.auth import AuthContext, api_keys, parse_bearer
from kryptodb.daemon.sessions import SessionState, sessions, SessionKeyring, keyring
from kryptodb.daemon import event_bus

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
    for p in req.active_partitions:
        key = encryptor._get_or_create_key(p)
        keyring.add_key(p, key)
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
    session = get_request_session(context, x_kryptodb_session) if isinstance(context, AuthContext) else None
    res = memory_service.query_facts(
        agent_id=agent_id,
        requested_scope=requested_scope,
        subject_id=subject_id,
        query=query,
        session=session,
        context=context,
    )
    if res.get("status") == "forbidden":
        raise HTTPException(status_code=403, detail=res.get("detail", "Forbidden"))
    return res


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
    from kryptodb.memory_api import get_agent
    
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

from kryptodb.graph import Edge
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

# --- Core Verbs API Surface (Phase 6) ---
class RememberRequest(BaseModel):
    agent_id: str = "Agent-1"
    fact: dict
    scope: str
    session_id: str | None = None

class RecallRequest(BaseModel):
    query: str | None = None
    scope: str | None = None
    subject_id: str | None = None
    session_id: str | None = None

class ForgetRequest(BaseModel):
    scope: str
    fact_id: str | None = None
    subject: str | None = None
    predicate: str | None = None
    approved: bool = False

@router.post("/memory/remember")
def post_memory_remember(
    req: RememberRequest,
    background_tasks: BackgroundTasks,
    context: AuthContext = Depends(require_auth),
    x_trace_id: str | None = Header(default=None)
):
    """Core Verb: remember(fact, scope, session_id?)"""
    from kryptodb.memory_api.service import memory_service
    from kryptodb.storage.redis_client import WriteLockError
    try:
        res = memory_service.remember(
            agent_id=req.agent_id,
            fact=req.fact,
            scope=req.scope,
            session_id=req.session_id,
            context=context,
            background_tasks=background_tasks,
            trace_id=x_trace_id
        )
    except WriteLockError:
        raise HTTPException(status_code=409, detail="Conflict: Concurrent write lock active")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if res.get("status") == "forbidden":
        raise HTTPException(status_code=403, detail=res.get("detail", "Forbidden"))
    elif res.get("status") == "hitl_required":
        raise HTTPException(status_code=422, detail=res)
    return res

@router.post("/memory/recall")
def post_memory_recall(
    req: RecallRequest,
    context: AuthContext = Depends(require_auth),
    x_kryptodb_session: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None)
):
    """Core Verb: recall(query, scope, session_id?)"""
    from kryptodb.memory_api.service import memory_service
    agent_id = context.agent_id if (isinstance(context, AuthContext) and not context.is_admin) else "admin"
    session_id = req.session_id or x_kryptodb_session
    res = memory_service.recall(
        agent_id=agent_id,
        query=req.query,
        scope=req.scope,
        subject_id=req.subject_id,
        session_id=session_id,
        context=context,
        trace_id=x_trace_id
    )
    if res.get("status") == "forbidden":
        raise HTTPException(status_code=403, detail=res.get("detail", "Forbidden"))
    return res

@router.post("/memory/forget")
def post_memory_forget(
    req: ForgetRequest,
    context: AuthContext = Depends(require_auth),
    x_kryptodb_session: str | None = Header(default=None),
    x_trace_id: str | None = Header(default=None)
):
    """Core Verb: forget(fact_id | subject+predicate, scope)"""
    from kryptodb.memory_api.service import memory_service
    agent_id = context.agent_id if (isinstance(context, AuthContext) and not context.is_admin) else "admin"
    res = memory_service.forget(
        agent_id=agent_id,
        scope=req.scope,
        fact_id=req.fact_id,
        subject=req.subject,
        predicate=req.predicate,
        session_id=x_kryptodb_session,
        context=context,
        approved=req.approved,
        trace_id=x_trace_id
    )
    if res.get("status") == "forbidden":
        raise HTTPException(status_code=403, detail=res.get("detail", "Forbidden"))
    elif res.get("status") == "hitl_required":
        raise HTTPException(status_code=422, detail=res)
    elif res.get("status") == "invalid_request":
        raise HTTPException(status_code=400, detail=res.get("detail", "Invalid request"))
    return res

@router.post("/memory/improve")
def post_memory_improve(session_id: str | None = None):
    """Core Verb: improve(session_id) [Designed-but-not-built]"""
    return {
        "status": "not_implemented",
        "message": "Session distillation (improve) is a designed-but-not-built operation scheduled for future release."
    }

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
                async for mode, payload in graph.astream(input_state, config, stream_mode=["messages", "updates"]):
                        
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

# Note: /dedup and GDS KNN clustering were excised in Phase 5 as dead code



class ReclassifyRequest(BaseModel):
    node_id: str | None = None
    knowledge_id: str | None = None
    to_scope: str


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
            from kryptodb.daemon.sessions import SessionKeyring
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
            from kryptodb.daemon.sessions import SessionKeyring
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

# Note: /ws/graph-events WebSocket stream was retired in Phase 4 in favor of REST polling (/graph/snapshot)

# --- App Definition ---
app = FastAPI(title="KryptoDB Daemon", version="0.1.0", lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID") or request.headers.get("X-Request-ID") or f"trace-{uuid.uuid4().hex[:12]}"
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    return response

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
    if not configured:
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
    from kryptodb.memory_api import get_agent
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
        if encryptor.customer_keys_required:
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


@app.post("/admin/sessions/{session_id}/revoke")
def revoke_session(session_id: str, context: AuthContext = Depends(require_admin)):
    session = sessions.get(session_id)
    if not session:
        if session_id in sessions._sessions:
            sessions.revoke(session_id)
            return {"status": "revoked", "session_id": session_id}
        raise HTTPException(status_code=404, detail="Session not found or already revoked")

    scopes_to_clean = set(session.scopes)
    sessions.revoke(session_id)

    # Prune from keyring if no other active sessions need these scopes
    active_scopes_remaining = set()
    for s in sessions._sessions.values():
        if not s.is_expired():
            active_scopes_remaining.update(s.scopes)

    for scope in scopes_to_clean:
        if scope not in active_scopes_remaining:
            keyring.remove_key(scope)

    return {"status": "revoked", "session_id": session_id}


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
