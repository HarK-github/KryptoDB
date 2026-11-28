from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from kryptodb.core.context import RequestContext
from kryptodb.core.filters import FilterBuilder
from kryptodb.infrastructure.audit_ledger import audit_ledger
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.queries import (
    GRAPH_SNAPSHOT_EDGES_QUERY,
    GRAPH_SNAPSHOT_NODES_QUERY,
)
from kryptodb.interfaces.http.middleware.context import authorize_or_403, require_context

logger = logging.getLogger("kryptodb.interfaces.graph")

router = APIRouter(prefix="/graph", tags=["Graph"])


@router.get("/snapshot")
async def get_graph_snapshot(
    agent_id: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    context: RequestContext = Depends(require_context),
):
    effective_tenant = tenant_id if (context.is_system and tenant_id) else (tenant_id or context.tenant_id)
    authorize_or_403(
        context,
        "graph:read",
        target_tenant=effective_tenant,
        audit_ledger=audit_ledger,
    )

    ctx = RequestContext.create(
        tenant_id=effective_tenant,
        agent_id=context.agent_id,
        active_scopes=context.active_scopes,
        is_system=context.is_system,
        role=context.role,
        permissions=context.permissions,
    )
    edge_filter, filter_params = FilterBuilder.build(
        ctx,
        alias="r",
        system_mode=context.is_system and effective_tenant == "system",
    )

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    try:
        if neo4j_client.check_health():
            query_tenant = None if (context.is_system and effective_tenant == "system") else effective_tenant
            res_nodes, _, _ = neo4j_client.execute_query_with_retry(
                GRAPH_SNAPSHOT_NODES_QUERY,
                tenant_id=query_tenant,
            )
            for record in res_nodes:
                nodes.append({
                    "id": record["id"],
                    "name": record["name"],
                    "label": record["name"] or record["id"],
                    "kind": record["kind"] or "Entity",
                    "type": record["kind"] or "Entity",
                    "tenant_id": record["tenant_id"],
                    "embedding_status": "384-dim (Cosine)" if record["has_embedding"] else "Pending",
                    "is_encrypted": os.getenv("KRYPTODB_STORAGE_MODE", "plaintext").lower() == "encrypted",
                })

            edges_query = GRAPH_SNAPSHOT_EDGES_QUERY.replace("{filter_clause}", edge_filter)
            res_edges, _, _ = neo4j_client.execute_query_with_retry(
                edges_query,
                **filter_params,
            )
            for record in res_edges:
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
                    "is_encrypted": os.getenv("KRYPTODB_STORAGE_MODE", "plaintext").lower() == "encrypted",
                })
    except Exception as exc:
        logger.warning("Error fetching Neo4j graph snapshot: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Graph storage unavailable")

    return {"nodes": nodes, "edges": edges}
