"""Phase 2 Gate Tests: Fail-closed SQLite key store, atomic Cardinality.ONE write, and GDPR revoke_by_user."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from kryptodb.core.context import RequestContext
from kryptodb.core.identity.authentication import Authenticator
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.infrastructure.neo4j import queries
from kryptodb.infrastructure.neo4j.store import Neo4jMemoryStore


def test_authenticator_fails_closed_on_sqlite_error(tmp_path) -> None:
    """T2.1: Authenticator must raise RuntimeError if file-backed SQLite fails to open (no silent :memory: fallback)."""
    bad_path = tmp_path / "api_keys.db"
    with patch("kryptodb.core.identity.authentication.sqlite3.connect", side_effect=sqlite3.OperationalError("disk I/O error")):
        with pytest.raises(RuntimeError, match="Failed to initialize SQLite"):
            Authenticator(db_path=bad_path)


def test_authenticator_allows_explicit_in_memory() -> None:
    """T2.1: Explicit ':memory:' db_path is allowed for unit tests."""
    auth = Authenticator(db_path=":memory:")
    assert auth._db_path == ":memory:"


def test_atomic_write_cardinality_one_query_and_service_wiring() -> None:
    """T2.2: ATOMIC_WRITE_CARDINALITY_ONE_QUERY exists and MemoryService routes Cardinality.ONE through propose_atomic."""
    assert hasattr(queries, "ATOMIC_WRITE_CARDINALITY_ONE_QUERY")
    q = queries.ATOMIC_WRITE_CARDINALITY_ONE_QUERY
    assert "_write_lock" in q
    assert "OPTIONAL MATCH" in q
    assert "superseded" in q
    assert "conflicted" in q

    mock_store = MagicMock()
    mock_store.propose_atomic.return_value = MagicMock(id="fact-atomic-1")
    svc = MemoryService(store=mock_store)
    ctx = RequestContext.create(tenant_id="t1", agent_id="a1", active_scopes=["default"])
    res = svc.propose_memory("Alice", "LIVES_IN", "Paris", context=ctx, confidence=0.9)
    assert res.id == "fact-atomic-1"
    mock_store.propose_atomic.assert_called_once()


def test_revoke_by_user_scrubs_predicate_and_respects_scopes() -> None:
    """T2.3: REVOKE_BY_USER_QUERY sets predicate='REVOKED', nulls description/embedding, and respects scopes."""
    q = queries.REVOKE_BY_USER_QUERY
    assert "r.predicate = 'REVOKED'" in q
    assert "r.description = NULL" in q
    assert "r.embedding = NULL" in q
    assert "$is_system" in q and "$active_scopes" in q

    mock_client = MagicMock()
    mock_client.execute_query_with_retry.return_value = ([{"revoked_count": 2}], None, None)
    store = Neo4jMemoryStore(client=mock_client, embedder=MagicMock())
    ctx = RequestContext.create(tenant_id="t1", agent_id="a1", active_scopes=["research"], is_system=False)
    count = store.revoke_by_user("user-42", ctx)
    assert count == 2
    assert mock_client.execute_query_with_retry.call_count == 3
    first_call_kwargs = mock_client.execute_query_with_retry.call_args_list[0].kwargs
    assert first_call_kwargs["is_system"] is False
    assert first_call_kwargs["active_scopes"] == ["research"]
