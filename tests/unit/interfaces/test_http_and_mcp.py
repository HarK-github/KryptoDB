from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from kryptodb.daemon.app import app
from kryptodb.interfaces.mcp.server import query_memory, propose_fact, session_activate, session_show
from kryptodb.core.retrieval.ranking import AttributedMemoryResult, ProvenanceStep

client = TestClient(app)

def test_health_endpoints():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    response = client.get("/ready")
    assert response.status_code == 200
    assert "status" in response.json()

@patch("kryptodb.interfaces.http.routes.memory.retrieval_service.search")
def test_memory_search_endpoint(mock_search):
    mock_search.return_value = [
        AttributedMemoryResult(
            fact_id="fact-1",
            summary="Alice MANAGES Bob",
            hops=1,
            final_score=0.92,
            relevance_score=0.95,
            confidence=1.0,
            recency_score=1.0,
            citation_tag="[Scope: default, Edge: fact-1]",
            provenance_path=[
                ProvenanceStep(hop=1, source="Alice", predicate="MANAGES", target="Bob", edge_id="fact-1")
            ]
        )
    ]

    response = client.post(
        "/api/v1/memory/search",
        json={"query": "Who does Alice manage?", "max_hops": 2, "top_k": 5}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["summary"] == "Alice MANAGES Bob"
    assert data[0]["hops"] == 1

@patch("kryptodb.interfaces.http.routes.memory.memory_service.write_fact")
def test_memory_write_endpoint(mock_write):
    from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
    from datetime import datetime, timezone

    mock_write.return_value = MemoryRecord(
        id="fact-new-123",
        subject="Bob",
        predicate="HAS_ADMIN_ACCESS",
        object="Database Cluster X",
        scope="PROJECT:alpha",
        tenant_id="default",
        status=MemoryStatus.ACTIVE,
        confidence=1.0,
        valid_from=datetime.now(timezone.utc)
    )

    response = client.post(
        "/api/v1/memory/write",
        json={
            "subject": "Bob",
            "predicate": "HAS_ADMIN_ACCESS",
            "object": "Database Cluster X",
            "scope": "PROJECT:alpha",
            "confidence": 1.0
        }
    )
    assert response.status_code == 200
    assert response.json()["id"] == "fact-new-123"
    assert response.json()["predicate"] == "HAS_ADMIN_ACCESS"

def test_admin_api_key_generation():
    # Calling admin endpoint with dev admin credentials
    response = client.post(
        "/admin/api-keys",
        headers={"Authorization": "Bearer dev-admin-key"},
        json={"agent_id": "Agent-2", "tenant_id": "tenant-custom", "scopes": ["research"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["api_key"].startswith("kdb-")
    assert data["agent_id"] == "Agent-2"
    assert data["tenant_id"] == "tenant-custom"

@patch("kryptodb.interfaces.mcp.server.retrieval_service.search")
def test_mcp_query_and_propose(mock_search):
    mock_search.return_value = [
        AttributedMemoryResult(
            fact_id="e-42",
            summary="Alice OWNS Project Alpha",
            hops=1,
            final_score=0.98,
            relevance_score=1.0,
            confidence=1.0,
            recency_score=1.0,
            citation_tag="[Scope: default, Edge: e-42]",
            provenance_path=[
                ProvenanceStep(hop=1, source="Alice", predicate="OWNS", target="Project Alpha", edge_id="e-42")
            ]
        )
    ]

    out_str = query_memory(query="What does Alice own?")
    parsed = json.loads(out_str)
    assert len(parsed) == 1
    assert parsed[0]["summary"] == "Alice OWNS Project Alpha"

    # MCP session tools
    act_res = json.loads(session_activate(["research", "operations"]))
    assert "research" in act_res["active_scopes"]
    assert "operations" in act_res["active_scopes"]

    show_res = json.loads(session_show())
    assert "research" in show_res["active_scopes"]

    # Test MCP propose_fact
    res = json.loads(propose_fact("Bob", "CONTRIBUTES_TO", "Project Alpha", scope="default"))
    assert "fact_id" in res
    assert res["status"] in ["active", "proposed"]

def test_graph_snapshot_endpoint():
    response = client.get("/graph/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) > 0

def test_ontology_endpoint():
    response = client.get("/ontology")
    assert response.status_code == 200
    data = response.json()
    assert "predicates" in data
    assert "content" in data
    pred_names = [p["name"] for p in data["predicates"]]
    assert "WORKS_FOR" in pred_names
    assert "LIVES_IN" in pred_names

def test_agents_and_audit_endpoints():
    res_agents = client.get("/agents")
    assert res_agents.status_code == 200
    agents = res_agents.json()
    assert len(agents) > 0
    assert any(a["id"] == "Agent-1" for a in agents)

    res_audit = client.get("/audit")
    assert res_audit.status_code == 200
    audit = res_audit.json()
    assert len(audit) > 0
