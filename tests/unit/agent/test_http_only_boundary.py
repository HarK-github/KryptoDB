"""Phase 3 Gate Tests: Enforce HTTP-only agent boundary and zero hardcoded demo entities."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from kryptodb.agent_runtime.langgraph.graph import (
    propose_fact_tool,
    revoke_fact_tool,
    synthesize_graph_answer,
)


def test_propose_fact_tool_fails_closed_on_403_and_503() -> None:
    """T3.2: propose_fact_tool returns {'status': 'error', ...} on HTTP 403/503 with zero in-process writes."""
    resp_403 = MagicMock(status_code=403, text="Forbidden")
    with patch("kryptodb.agent_runtime.langgraph.graph.httpx.post", return_value=resp_403):
        out = json.loads(propose_fact_tool.invoke({"subject": "Alice", "predicate": "LIVES_IN", "obj": "Paris"}))
    assert out["status"] == "error"
    assert out["code"] == 403

    with patch("kryptodb.agent_runtime.langgraph.graph.httpx.post", side_effect=ConnectionError("Connection refused")):
        out_503 = json.loads(propose_fact_tool.invoke({"subject": "Alice", "predicate": "LIVES_IN", "obj": "Paris"}))
    assert out_503["status"] == "error"
    assert out_503["code"] == 503


def test_revoke_fact_tool_fails_closed_on_403_and_503() -> None:
    """T3.2: revoke_fact_tool returns {'status': 'error', ...} on HTTP 403/503 with zero in-process writes."""
    resp_403 = MagicMock(status_code=403, text="Forbidden")
    with patch("kryptodb.agent_runtime.langgraph.graph.httpx.delete", return_value=resp_403):
        out = json.loads(revoke_fact_tool.invoke({"fact_id": "fact-123"}))
    assert out["status"] == "error"
    assert out["code"] == 403

    with patch("kryptodb.agent_runtime.langgraph.graph.httpx.delete", side_effect=ConnectionError("Connection refused")):
        out_503 = json.loads(revoke_fact_tool.invoke({"fact_id": "fact-123"}))
    assert out_503["status"] == "error"
    assert out_503["code"] == 503


def test_synthesize_graph_answer_is_generic() -> None:
    """T3.1: synthesize_graph_answer walks arbitrary triples without hardcoded demo names."""
    facts = [
        {"subject": "Service A", "predicate": "DEPENDS_ON", "object": "Service B"},
        {"subject": "Service B", "predicate": "USES", "object": "PostgreSQL"},
    ]
    ans = synthesize_graph_answer("Trace upstream failure for Service A", facts)
    assert "Service A" in ans
    assert "Service B" in ans
    assert "PostgreSQL" in ans
    assert "Billing Engine" not in ans
    assert "Ledger Prime" not in ans
