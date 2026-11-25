from __future__ import annotations

import os
os.environ["KRYPTODB_ADMIN_API_KEY"] = "test-admin-key"
os.environ["KRYPTODB_API_KEY"] = "test-admin-key"
"""Contract parity tests: Asserting identical service calls and compatible JSON shapes between FastMCP and REST."""

import json
from unittest.mock import patch
from fastapi.testclient import TestClient

from kryptodb.daemon.app import app
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.retrieval.ranking import AttributedMemoryResult, ProvenanceStep
from kryptodb.interfaces.mcp.server import (
    propose_memory as mcp_propose_memory,
    query_memory as mcp_query_memory,
    forget_memory as mcp_forget_memory,
)

client = TestClient(app)
client.headers.update({"Authorization": "Bearer test-admin-key"})


@patch("kryptodb.interfaces.http.routes.v1_routes.memory_service.propose_memory")
@patch("kryptodb.interfaces.mcp.server.memory_service.propose_memory")
def test_propose_memory_parity(mcp_mock, rest_mock):
    sample_record = MemoryRecord(
        id="fact-101",
        subject="Alice",
        predicate="WORKS_FOR",
        object="Acme Corp",
        tenant_id="default",
        scope="default",
        status=MemoryStatus.ACTIVE,
        version=1,
    )
    mcp_mock.return_value = sample_record
    rest_mock.return_value = sample_record

    # 1. MCP Call
    mcp_raw = mcp_propose_memory(
        subject="Alice",
        predicate="WORKS_FOR",
        obj="Acme Corp",
    )
    mcp_data = json.loads(mcp_raw)

    # 2. REST Call
    response = client.post(
        "/v1/memory/propose",
        json={"subject": "Alice", "predicate": "WORKS_FOR", "object": "Acme Corp"},
    )
    assert response.status_code == 200
    rest_data = response.json()

    # Parity assertions
    assert mcp_data["fact_id"] == rest_data["id"]
    assert mcp_data["subject"] == rest_data["subject"]
    assert mcp_data["predicate"] == rest_data["predicate"]
    assert mcp_data["object"] == rest_data["object"]
    assert mcp_data["status"] == rest_data["status"]
    assert mcp_data["version"] == rest_data["version"]


@patch("kryptodb.interfaces.http.routes.v1_routes.retrieval_service.search")
@patch("kryptodb.interfaces.mcp.server.retrieval_service.search")
def test_search_memory_parity(mcp_mock, rest_mock):
    sample_result = [
        AttributedMemoryResult(
            fact_id="fact-101",
            summary="Alice WORKS_FOR Acme Corp",
            hops=1,
            final_score=0.95,
            relevance_score=0.98,
            confidence=1.0,
            recency_score=1.0,
            citation_tag="[tenant:default, scope:default, edges:fact-101]",
            provenance_path=[
                ProvenanceStep(hop=1, source="Alice", predicate="WORKS_FOR", target="Acme Corp", edge_id="fact-101")
            ],
        )
    ]
    mcp_mock.return_value = sample_result
    rest_mock.return_value = sample_result

    # 1. MCP Call
    mcp_raw = mcp_query_memory(query="Where does Alice work?", top_k=5)
    mcp_data = json.loads(mcp_raw)

    # 2. REST Call
    response = client.post(
        "/v1/memory/search",
        json={"query": "Where does Alice work?", "top_k": 5},
    )
    assert response.status_code == 200
    rest_data = response.json()

    # Parity assertions
    assert len(mcp_data) == len(rest_data) == 1
    assert mcp_data[0]["summary"] == rest_data[0]["summary"]
    assert mcp_data[0]["hops"] == rest_data[0]["hops"]
    assert mcp_data[0]["citation"] == rest_data[0]["citation_tag"]
    assert mcp_data[0]["score"] == rest_data[0]["final_score"]


@patch("kryptodb.interfaces.http.routes.v1_routes.memory_service.forget_memory")
@patch("kryptodb.interfaces.mcp.server.memory_service.forget_memory")
def test_forget_memory_parity(mcp_mock, rest_mock):
    # 1. MCP Call
    mcp_raw = mcp_forget_memory(id="fact-999")
    mcp_data = json.loads(mcp_raw)

    # 2. REST Call
    response = client.delete("/v1/memory/fact-999")
    assert response.status_code == 200
    rest_data = response.json()

    assert mcp_data["id"] == rest_data["id"] == "fact-999"
    assert mcp_data["status"] in ("revoked", "forgotten")
