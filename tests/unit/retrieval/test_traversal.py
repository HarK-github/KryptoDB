"""Unit tests for Phase 6 MultiHop graph traversal with cycle and hub suppression."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from kryptodb.core.context import RequestContext
from kryptodb.core.retrieval.traversal import MultiHopTraverser


def test_traversal_cycle_suppression_and_hub_limit():
    """Verify that multi-hop Cypher queries include cycle control (s <> t AND s <> mid AND mid <> t)

    and hub suppression (degree <= max_hub_degree).
    """
    mock_client = MagicMock()
    executed_queries = []

    def fake_execute(query, **kwargs):
        executed_queries.append((query, kwargs))
        return ([], None, None)

    mock_client.execute_query_with_retry = fake_execute

    traverser = MultiHopTraverser(client=mock_client, max_hub_degree=50)
    ctx = RequestContext.create(tenant_id="acme", agent_id="agent-1", active_scopes=["default"])

    traverser.traverse_from_seeds(["Alice"], ctx)

    assert len(executed_queries) == 2
    query_1hop, kwargs_1hop = executed_queries[0]
    query_2hop, kwargs_2hop = executed_queries[1]

    # 1-hop checks
    assert "ORDER BY e.confidence DESC" in query_1hop
    assert "LIMIT $limit_1hop" in query_1hop

    # 2-hop cycle and hub checks
    assert "s <> t AND s <> mid AND mid <> t" in query_2hop
    assert "size([(mid)-[:FACT]-() | 1]) <= $max_hub_degree" in query_2hop
    assert kwargs_2hop["max_hub_degree"] == 50
