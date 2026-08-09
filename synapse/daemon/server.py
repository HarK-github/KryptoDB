"""
Unified KryptoDB Daemon server.
"""
from fastapi import FastAPI, HTTPException, APIRouter, BackgroundTasks
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List
import logging
import uvicorn

from synapse.daemon.keyring import keyring
from synapse.integrity.partition_crypto import Encryptor
from synapse.storage.neo4j_client import neo4j_client
from synapse.storage.redis_client import redis_client
from synapse.storage.bootstrap import bootstrap_storage

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

class SessionStartRequest(BaseModel):
    active_partitions: List[str]

from synapse.daemon.events import event_bus

@router.get("/audit")
def get_audit_log():
    return event_bus.audit_log

@router.get("/agents")
def get_agents():
    import json
    try:
        with open("data/agents.json", "r") as f:
            return json.load(f)
    except Exception:
        return {}

@router.get("/ontology")
def get_ontology():
    try:
        with open("data/ontology.toml", "r") as f:
            return {"content": f.read()}
    except Exception:
        return {"content": ""}

@router.post("/session")
def start_session(req: SessionStartRequest):
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
def get_session():
    return {"active_partitions": list(keyring._keys.keys())}

@router.get("/memory/{agent_id}")
def get_memory(agent_id: str, background_tasks: BackgroundTasks, query: str = None, requested_scope: str = None, subject_id: str = None, mode: str = "local"):
    from synapse.memory_api.agents import get_agent
    from synapse.graph.read_engine import ReadEngine
    agent = get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=403, detail="Unknown agent")
    
    if requested_scope:
        if requested_scope not in agent["allowed_scopes"]:
            return {"status": "denied_permission", "facts": []}
        if requested_scope not in keyring._keys:
            return {"status": "denied_inactive", "facts": []}
        scopes_to_query = [requested_scope]
    else:
        scopes_to_query = agent["allowed_scopes"]

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
    effective_scopes = list(set(scopes_to_query) & set(keyring._keys.keys()))
    if not effective_scopes:
        return {"status": "denied_inactive", "facts": []}

    read_engine = ReadEngine()
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

@router.get("/graph/snapshot")
def get_graph_snapshot(agent_id: str):
    from synapse.memory_api.agents import get_agent
    from synapse.daemon.keyring import keyring
    from synapse.storage.neo4j_client import neo4j_client
    
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
    if agent_id == "admin":
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
def get_task_status(task_id: str):
    from synapse.storage.redis_client import redis_client
    status = redis_client.client.get(f"task:{task_id}")
    if not status:
        return {"task_id": task_id, "status": "not_found"}
    if isinstance(status, bytes):
        status = status.decode('utf-8')
    return {"task_id": task_id, "status": status}

@router.post("/memory")
def post_memory(req: ProposeFactRequest, background_tasks: BackgroundTasks):
    from synapse.memory_api.propose import propose_fact
    from synapse.memory_api.conflict import detect_conflict
    
    edge = Edge(**req.edge)
    
    # Prompt injection content-flagging heuristic (Phase 8.1)
    suspicious_patterns = [
        "ignore previous", "system prompt", "bypass", "you are now",
        "<script>", "sql injection", "drop table"
    ]
    
    content_to_check = f"{edge.subject_id} {edge.predicate} {edge.object_id}".lower()
    if any(pattern in content_to_check for pattern in suspicious_patterns):
        edge.is_suspicious = True
        logger.warning(f"Flagged suspicious content in proposed edge {edge.id}")

    existing_edges = detect_conflict(edge.subject_id, edge.predicate, edge.object_id, edge.permission_scope)
    
    status, committed_edge, cascade_args = propose_fact(req.agent_id, edge, False, existing_edges)
    
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
def onboard_agent(req: OnboardRequest):
    from synapse.memory_api.onboarding import auto_onboard_agent
    res = auto_onboard_agent(req.agent_id, req.sample_facts, req.trust_prior)
    return res

class ReclassifyRequest(BaseModel):
    node_id: str
    to_scope: str

@router.post("/reclassify")
def reclassify_node(req: ReclassifyRequest):
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
        plaintext_name = encryptor.decrypt(old_scope, name_ct)
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
    await websocket.accept()
    from synapse.memory_api.agents import get_agent
    from synapse.daemon.keyring import keyring
    
    agent = get_agent(agent_id)
    if not agent and agent_id != "admin":
        await websocket.close(code=1008, reason="Unknown agent")
        return
        
    client_id = str(uuid.uuid4())
    queue = await event_bus.connect(client_id)
    try:
        while True:
            event: GraphEvent = await queue.get()
            if agent:
                allowed_scopes = set(agent.get("allowed_scopes", []))
            else:
                # "admin" has all scopes implicitly
                allowed_scopes = {event.permission_scope}

            active_partitions = set(keyring._keys.keys())
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
def admin_graph():
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
def admin_scopes():
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

class UpdateAgentRequest(BaseModel):
    agent_id: str
    scopes: List[str]

@app.post("/admin/agents")
def update_agent(req: UpdateAgentRequest):
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
        
    return {"status": "ok", "agent_id": req.agent_id, "scopes": req.scopes}

app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
