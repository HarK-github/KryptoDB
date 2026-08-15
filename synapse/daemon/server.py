"""
Unified KryptoDB Daemon server.
"""
from fastapi import FastAPI, HTTPException, APIRouter, BackgroundTasks, Depends, Header
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List
import logging
import os
import uvicorn
from pathlib import Path

from synapse.daemon.keyring import keyring
from synapse.integrity.partition_crypto import Encryptor
from synapse.storage.neo4j_client import neo4j_client
from synapse.storage.redis_client import redis_client
from synapse.storage.bootstrap import bootstrap_storage
from synapse.daemon.auth import AuthContext, api_keys, parse_bearer
from synapse.daemon.sessions import SessionState, sessions
from synapse.integrity.knowledge_migration import migrate_legacy_fact

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

from synapse.daemon.events import event_bus

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
    
    from synapse.memory_api.session_search import session_index
    session_index.clear()
    
    for p in req.active_partitions:
        key = encryptor._get_or_create_key(p)
        keyring.add_key(p, key)
        
    # Stream decrypted names into Bloom filter
    from synapse.storage.neo4j_client import neo4j_client
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
def get_session(context: AuthContext = Depends(require_auth), x_synapse_session: str | None = Header(default=None)):
    if isinstance(context, AuthContext):
        session = get_request_session(context, x_synapse_session)
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
def get_memory(agent_id: str, background_tasks: BackgroundTasks, query: str = None, requested_scope: str = None, subject_id: str = None, mode: str = "local", context: AuthContext = Depends(require_auth), x_synapse_session: str | None = Header(default=None)):
    from synapse.memory_api.agents import get_agent
    from synapse.graph.read_engine import ReadEngine
    if isinstance(context, AuthContext):
        if not context.is_admin and context.agent_id != agent_id:
            raise HTTPException(status_code=403, detail="Agent identity is bound to the API key")
        session = get_request_session(context, x_synapse_session)
        active_scopes = set(session.keys) if session else set()
        if context.is_admin:
            agent = {"id": "admin", "allowed_scopes": []}
        else:
            agent = get_agent(context.agent_id)
            if not agent:
                raise HTTPException(status_code=403, detail="Unknown agent")
    else:
        session = None
        active_scopes = set(keyring._keys)
        agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=403, detail="Unknown agent")
    
    if requested_scope:
        allowed = set(agent.get("allowed_scopes", [])) if not (isinstance(context, AuthContext) and context.is_admin) else active_scopes
        if requested_scope not in allowed:
            return {"status": "denied_permission", "facts": []}
        if requested_scope not in active_scopes and not (isinstance(context, AuthContext) and context.is_admin):
            return {"status": "denied_inactive", "facts": []}
        scopes_to_query = [requested_scope]
    else:
        scopes_to_query = sorted(set(agent.get("allowed_scopes", [])) & (active_scopes or set(agent.get("allowed_scopes", []))))

    if mode == "global":
        from synapse.graph.gds_engine import GDSEngine
        gds = GDSEngine()
        communities = gds.get_communities(scopes_to_query)
        if not communities:
            # Trigger detection if communities are empty
            for scope in scopes_to_query:
                gds.run_louvain_community_detection(scope)
            communities = gds.get_communities(scopes_to_query)
            
        return {"status": "ok", "mode": "global", "communities": communities}

    # Verify that at least one allowed scope is currently active in RAM
    from synapse.daemon.keyring import keyring
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
        
    from synapse.daemon.events import event_bus, GraphEvent
    from datetime import datetime
    
    async def publish_events():
        for fact in facts:
            scope = fact.get("permission_scope", "research")
            await event_bus.publish(GraphEvent(
                event_type="traversal_step",
                permission_scope=scope,
                timestamp=datetime.utcnow(),
                edge_id=fact.get("edge_id") or fact.get("id")
            ))
            
    background_tasks.add_task(publish_events)
    
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
    x_synapse_session: str | None = Header(default=None),
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
        x_synapse_session=x_synapse_session,
    )

@router.get("/graph/snapshot")
def get_graph_snapshot(agent_id: str, context: AuthContext = Depends(require_auth), x_synapse_session: str | None = Header(default=None)):
    from synapse.memory_api.agents import get_agent
    from synapse.daemon.keyring import keyring
    from synapse.storage.neo4j_client import neo4j_client

    if isinstance(context, AuthContext) and not context.is_admin and context.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Agent identity is bound to the API key")
    
    if not neo4j_client.driver:
        return {"nodes": [], "edges": []}
        
    agent = get_agent(agent_id)
    if not agent:
        if agent_id == "admin":
            query = "MATCH (n) RETURN DISTINCT n.permission_scope AS scope UNION MATCH ()-[r]->() RETURN DISTINCT r.permission_scope AS scope"
            scopes_res, _, _ = neo4j_client.execute_query_with_retry(query)
            allowed_scopes = {r["scope"] for r in scopes_res if r["scope"]}
        else:
            raise HTTPException(status_code=403, detail="Unknown agent")
    else:
        allowed_scopes = set(agent.get("allowed_scopes", []))
    
    active_partitions = set(keyring._keys.keys())
    if isinstance(context, AuthContext) and not context.is_admin:
        session = get_request_session(context, x_synapse_session)
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
    RETURN n.id AS id, labels(n)[0] AS type, n.permission_scope AS scope
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

from synapse.graph.schema import Edge
class ProposeFactRequest(BaseModel):
    agent_id: str
    edge: dict

from fastapi import BackgroundTasks
import uuid

def run_cascade_task(task_id: str, revoked_edge_id: str, valid_to_iso: str, superseded_by_id: str):
    from synapse.storage.redis_client import redis_client
    redis_client.client.set(f"task:{task_id}", "running", ex=3600)
    
    from synapse.graph.write_engine import WriteEngine
    try:
        engine = WriteEngine()
        engine._cascade_revocation(revoked_edge_id, valid_to_iso, superseded_by_id)
        redis_client.client.set(f"task:{task_id}", "completed", ex=3600)
    except Exception as e:
        redis_client.client.set(f"task:{task_id}", f"failed: {str(e)}", ex=3600)

@router.get("/status/{task_id}")
def get_task_status(task_id: str, context: AuthContext = Depends(require_auth)):
    from synapse.storage.redis_client import redis_client
    status = redis_client.client.get(f"task:{task_id}")
    if not status:
        return {"task_id": task_id, "status": "not_found"}
    if isinstance(status, bytes):
        status = status.decode('utf-8')
    return {"task_id": task_id, "status": status}

@router.post("/memory")
def post_memory(req: ProposeFactRequest, background_tasks: BackgroundTasks, context: AuthContext = Depends(require_auth)):
    from synapse.memory_api.propose import propose_fact
    from synapse.memory_api.conflict import detect_conflict
    
    edge = Edge(**req.edge)

    if isinstance(context, AuthContext):
        if not context.is_admin and req.agent_id != context.agent_id:
            raise HTTPException(status_code=403, detail="Agent identity is bound to the API key")
        if not context.is_admin and edge.permission_scope not in api_keys.effective_scopes(context):
            raise HTTPException(status_code=403, detail="Fact scope is outside API-key scope")
        proposer_id = context.agent_id
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

class DedupRequest(BaseModel):
    agent_id: str

def run_knn_task(task_id: str, scopes: list[str]):
    from synapse.storage.redis_client import redis_client
    redis_client.client.set(f"task:{task_id}", "running", ex=3600)
    
    from synapse.graph.gds_engine import GDSEngine
    try:
        gds = GDSEngine()
        for scope in scopes:
            gds.run_knn_similarity(scope)
        redis_client.client.set(f"task:{task_id}", "completed", ex=3600)
    except Exception as e:
        redis_client.client.set(f"task:{task_id}", f"failed: {str(e)}", ex=3600)

@router.post("/dedup")
def trigger_knn_dedup(req: DedupRequest, background_tasks: BackgroundTasks):
    from synapse.memory_api.agents import get_agent
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
    from synapse.memory_api.onboarding import auto_onboard_agent
    res = auto_onboard_agent(req.agent_id, req.sample_facts, req.trust_prior)
    return res

class ReclassifyRequest(BaseModel):
    node_id: str | None = None
    knowledge_id: str | None = None
    to_scope: str


class LegacyMigrationRequest(BaseModel):
    scopes: List[str]


@router.post("/maintenance/migrate-legacy")
def migrate_legacy_records(
    req: LegacyMigrationRequest,
    context: AuthContext = Depends(require_auth),
    x_synapse_session: str | None = Header(default=None),
):
    """Convert legacy facts without exposing their plaintext to the caller."""
    if not isinstance(context, AuthContext) or context.is_admin:
        raise HTTPException(status_code=403, detail="A scoped agent session is required")
    session = get_request_session(context, x_synapse_session)
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
            REMOVE r.predicate, r.source_agent_id
            """
            neo4j_client.execute_query_with_retry(
                update_query,
                edge_id=record["edge_id"],
                replacement=replacement,
            )
            migrated += 1
        except Exception:
            failed += 1

    return {
        "status": "ok" if failed == 0 else "partial",
        "scopes": sorted(requested),
        "examined": len(records),
        "migrated": migrated,
        "failed": failed,
    }

@router.post("/reclassify")
def reclassify_node(req: ReclassifyRequest, context: AuthContext = Depends(require_auth), x_synapse_session: str | None = Header(default=None)):
    session = None
    if isinstance(context, AuthContext) and not context.is_admin:
        session = get_request_session(context, x_synapse_session)
        if req.to_scope not in session.scopes:
            raise HTTPException(status_code=403, detail="Target scope is outside API-key scope")
    if req.knowledge_id:
        if session is None or req.to_scope not in session.keys:
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
        envelope = encryptor.decrypt_knowledge(
            old_scope,
            req.knowledge_id,
            old_props,
            session,
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
    query = "MATCH (n:Entity {id: $node_id}) RETURN n.permission_scope AS old_scope, n.name_ct AS name_ct"
    res, _, _ = neo4j_client.execute_query_with_retry(query, node_id=req.node_id)
    if not res:
        raise HTTPException(status_code=404, detail="Node not found")
        
    old_scope = res[0]["old_scope"]
    name_ct = res[0]["name_ct"]
    
    # 2. Decrypt with old partition key (requires active session)
    plaintext_name = None
    if name_ct:
        plaintext_name = encryptor.decrypt(old_scope, name_ct, session)
        if not plaintext_name:
            raise HTTPException(status_code=403, detail="Cannot decrypt node. Ensure old partition is active in session.")
    else:
        plaintext_name = req.node_id  # Fallback for unencrypted nodes
        
    # 3. Re-encrypt with new partition key
    new_ct = encryptor.encrypt(req.to_scope, plaintext_name)
    new_blind_idx = encryptor.compute_blind_idx(req.to_scope, plaintext_name)
    
    # 4. Update node in Neo4j
    update_q = """
    MATCH (n:Entity {id: $node_id})
    SET n.permission_scope = $to_scope,
        n.name_ct = $new_ct,
        n.name_blind_idx = $new_blind_idx
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
from synapse.daemon.events import event_bus, GraphEvent
import asyncio

@router.websocket("/ws/graph-events")
async def websocket_graph_events(websocket: WebSocket, agent_id: str):
    # Browsers cannot set arbitrary WebSocket headers. Accept the bearer key
    # through a negotiated subprotocol while retaining Authorization support
    # for native clients.
    bearer = parse_bearer(websocket.headers.get("authorization"))
    requested_protocols = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    protocol_key = next((p[len("synapse-bearer."):] for p in requested_protocols if p.startswith("synapse-bearer.")), None)
    context = api_keys.authenticate(bearer or protocol_key)
    if context is None:
        await websocket.close(code=1008, reason="Valid bearer API key required")
        return
    if not context.is_admin and context.agent_id != agent_id:
        await websocket.close(code=1008, reason="Agent identity is bound to the API key")
        return
    await websocket.accept(subprotocol=f"synapse-bearer.{protocol_key}" if protocol_key else None)
    from synapse.memory_api.agents import get_agent
    from synapse.daemon.keyring import keyring
    
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

            session = None if context.is_admin else sessions.get(websocket.headers.get("x-synapse-session"))
            active_partitions = set(session.keys) if session else set()
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
    allow_credentials=True,
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
    return {"keys": [statuses[scope] for scope in sorted(statuses)]}

class UpdateAgentRequest(BaseModel):
    agent_id: str
    scopes: List[str]

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
    from synapse.memory_api.agents import get_agent
    agent = get_agent(req.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Unknown agent")
    requested = set(req.scopes)
    if not requested.issubset(set(agent.get("allowed_scopes", []))):
        raise HTTPException(status_code=400, detail="Key scopes exceed agent allowed scopes")
    raw_key, record = api_keys.create_key(req.agent_id, sorted(requested), req.key_id)
    return {"status": "created", "api_key": raw_key, **record, "warning": "Store this key now; it will not be shown again."}


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
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

