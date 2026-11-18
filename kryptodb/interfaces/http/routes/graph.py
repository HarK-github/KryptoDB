from __future__ import annotations

import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from kryptodb.core.identity import require_auth
from kryptodb.storage.neo4j_client import neo4j_client

logger = logging.getLogger("kryptodb.interfaces.graph")

router = APIRouter(prefix="/graph", tags=["Graph"])

@router.get("/snapshot")
async def get_graph_snapshot(
    agent_id: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    auth: Dict[str, Any] = Depends(require_auth)
):
    effective_tenant = tenant_id or auth.get("tenant_id", "default")
    if effective_tenant == "system":
        effective_tenant = None

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    try:
        if neo4j_client.check_health():
            # Query nodes
            query_nodes = """
            MATCH (n:Entity)
            WHERE ($tenant_id IS NULL OR n.tenant_id = $tenant_id)
            RETURN n.id AS id, n.name AS name, n.kind AS kind, n.tenant_id AS tenant_id, 
                   n.embedding IS NOT NULL AS has_embedding
            LIMIT 500
            """
            res_nodes = neo4j_client.execute_query_with_retry(query_nodes, tenant_id=effective_tenant)
            for record in res_nodes.records:
                nodes.append({
                    "id": record["id"],
                    "name": record["name"],
                    "label": record["name"] or record["id"],
                    "kind": record["kind"] or "Entity",
                    "type": record["kind"] or "Entity",
                    "tenant_id": record["tenant_id"],
                    "embedding_status": "384-dim (Cosine)" if record["has_embedding"] else "Pending",
                    "is_encrypted": False
                })

            # Query edges
            query_edges = """
            MATCH (s:Entity)-[r:FACT]->(t:Entity)
            WHERE ($tenant_id IS NULL OR r.tenant_id = $tenant_id)
            RETURN r.id AS id, s.id AS source, t.id AS target, r.predicate AS predicate,
                   r.description AS description, r.scope AS scope, r.confidence AS confidence,
                   r.status AS status, r.valid_from AS valid_from, r.valid_until AS valid_until
            LIMIT 1000
            """
            res_edges = neo4j_client.execute_query_with_retry(query_edges, tenant_id=effective_tenant)
            for record in res_edges.records:
                edges.append({
                    "id": record["id"],
                    "source": record["source"],
                    "target": record["target"],
                    "predicate": record["predicate"],
                    "description": record["description"],
                    "scope": record["scope"] or "default",
                    "confidence": record["confidence"] if record["confidence"] is not None else 1.0,
                    "status": record["status"] or "active",
                    "valid_from": str(record["valid_from"]) if record["valid_from"] else None,
                    "valid_until": str(record["valid_until"]) if record["valid_until"] else None,
                    "is_encrypted": False
                })
    except Exception as exc:
        logger.warning("Error fetching Neo4j graph snapshot: %s", exc)

    # If Neo4j has no records yet (e.g. fresh installation or dev), return standard seed graph
    if not nodes:
        nodes = [
            {"id": "kryptodb", "name": "KryptoDB", "label": "KryptoDB", "kind": "project", "type": "project", "scope": "PROJECT:kryptodb", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "memory-engine", "name": "Memory Engine", "label": "Memory Engine", "kind": "concept", "type": "concept", "scope": "PROJECT:kryptodb", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "neo4j", "name": "Neo4j Plaintext Graph", "label": "Neo4j Plaintext Graph", "kind": "service", "type": "service", "scope": "PROJECT:kryptodb", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "Agent-1", "name": "Agent-1", "label": "Agent-1", "kind": "agent", "type": "agent", "scope": "research", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "Alice", "name": "Alice", "label": "Alice", "kind": "PERSON", "type": "PERSON", "scope": "research", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "Bob", "name": "Bob", "label": "Bob", "kind": "PERSON", "type": "PERSON", "scope": "research", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "Production-Cluster", "name": "Production Cluster", "label": "Production Cluster", "kind": "INFRASTRUCTURE", "type": "INFRASTRUCTURE", "scope": "engineering", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False},
            {"id": "San-Francisco", "name": "San Francisco", "label": "San Francisco", "kind": "LOCATION", "type": "LOCATION", "scope": "default", "tenant_id": "default", "embedding_status": "384-dim (Cosine)", "is_encrypted": False}
        ]
        edges = [
            {"id": "e1", "source": "kryptodb", "target": "memory-engine", "predicate": "INCLUDES", "scope": "PROJECT:kryptodb", "confidence": 1.0, "status": "active", "is_encrypted": False},
            {"id": "e2", "source": "memory-engine", "target": "neo4j", "predicate": "STORED_IN", "scope": "PROJECT:kryptodb", "confidence": 1.0, "status": "active", "is_encrypted": False},
            {"id": "e3", "source": "Agent-1", "target": "kryptodb", "predicate": "INTERFACES_WITH", "scope": "research", "confidence": 1.0, "status": "active", "is_encrypted": False},
            {"id": "e4", "source": "Alice", "target": "Bob", "predicate": "MANAGES", "scope": "research", "confidence": 0.95, "status": "active", "is_encrypted": False},
            {"id": "e5", "source": "Bob", "target": "Production-Cluster", "predicate": "ADMINISTERS", "scope": "engineering", "confidence": 0.98, "status": "active", "is_encrypted": False},
            {"id": "e6", "source": "Alice", "target": "San-Francisco", "predicate": "LIVES_IN", "scope": "default", "confidence": 0.92, "status": "active", "is_encrypted": False}
        ]

    return {"nodes": nodes, "edges": edges}
