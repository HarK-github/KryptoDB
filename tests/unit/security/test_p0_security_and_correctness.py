"""P0 Security, Isolation, Correctness, and Structural Safety-Net Tests."""

from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from kryptodb.core.identity.authentication import Authenticator
from kryptodb.infrastructure.audit_ledger import AuditLedger
from kryptodb.infrastructure.neo4j.queries import (
    ATOMIC_SUPERSEDE_QUERY,
    GRAPH_SNAPSHOT_EDGES_QUERY,
    WRITE_FACT_QUERY,
)
from tests.eval.metrics import compute_ndcg_at_k, compute_precision_at_k


def test_dev_admin_key_backdoor_is_disabled_in_production():
    prev_env = os.environ.get("KRYPTODB_ENV")
    prev_key = os.environ.get("KRYPTODB_ADMIN_API_KEY")
    try:
        os.environ["KRYPTODB_ENV"] = "production"
        os.environ["KRYPTODB_ADMIN_API_KEY"] = "prod-secret-key-999"
        auth = Authenticator()
        assert auth.verify_token("dev-admin-key") is None
        claims = auth.verify_token("prod-secret-key-999")
        assert claims is not None and claims["is_admin"] is True
    finally:
        if prev_env is None:
            os.environ.pop("KRYPTODB_ENV", None)
        else:
            os.environ["KRYPTODB_ENV"] = prev_env
        if prev_key is None:
            os.environ.pop("KRYPTODB_ADMIN_API_KEY", None)
        else:
            os.environ["KRYPTODB_ADMIN_API_KEY"] = prev_key


def test_list_keys_never_exposes_plaintext_secret():
    auth = Authenticator()
    raw_secret = "kdb-super-secret-value-12345"
    rec = auth.register_agent_key(raw_secret, agent_id="Agent-A", tenant_id="tenant-a", scopes=["research"])
    keys = auth.list_keys()
    assert len(keys) >= 1
    for item in keys:
        assert "full_key" not in item
        assert raw_secret not in str(item.values())
    assert auth.verify_token(raw_secret)["tenant_id"] == "tenant-a"
    assert auth.revoke_key(rec["key_id"]) is True
    assert auth.verify_token(raw_secret) is None


def test_ndcg_and_precision_bounded_to_1_even_with_duplicates():
    retrieved = ["fact-1", "fact-1", "fact-1", "fact-2"]
    ground_truth = {"fact-1"}
    ndcg = compute_ndcg_at_k(retrieved, ground_truth, k=10)
    prec = compute_precision_at_k(retrieved, ground_truth, k=10)
    assert 0.0 <= ndcg <= 1.0
    assert 0.0 <= prec <= 1.0
    assert ndcg == 1.0
    assert prec == 0.5


def test_audit_ledger_hash_chain_integrity():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = AuditLedger(db_path=os.path.join(tmp, "audit.db"))
        ledger.append(actor="Agent-1", action="propose_memory", tenant_id="t1", scope="research", target_id="fact-1")
        ledger.append(actor="admin", action="revoke_api_key", tenant_id="system", scope="*", target_id="key_123")
        assert ledger.verify_chain() is True
        t1_entries = ledger.list_recent(tenant_id="t1")
        assert len(t1_entries) == 1
        assert t1_entries[0]["actor"] == "Agent-1"


def test_atomic_supersede_query_locks_and_checks_active_status():
    assert "SET old_r._lock = true" in ATOMIC_SUPERSEDE_QUERY
    assert "old_r.status = 'active'" in ATOMIC_SUPERSEDE_QUERY
    assert "idempotency_key: $idempotency_key" in ATOMIC_SUPERSEDE_QUERY
    assert "idempotency_key: $idempotency_key" in WRITE_FACT_QUERY
    assert "{filter_clause}" in GRAPH_SNAPSHOT_EDGES_QUERY


def test_no_raw_cypher_in_conflicts_or_graph_route_or_revoke_tool():
    repo_root = Path(__file__).resolve().parents[3]
    for rel in [
        "kryptodb/core/memory/conflicts.py",
        "kryptodb/interfaces/http/routes/graph.py",
        "kryptodb/agent_runtime/langgraph/graph.py",
    ]:
        content = (repo_root / rel).read_text(encoding="utf-8")
        assert "MATCH (" not in content, f"Raw Cypher leaked in {rel}"
        assert "MATCH ()" not in content, f"Raw Cypher leaked in {rel}"


if __name__ == "__main__":
    test_dev_admin_key_backdoor_is_disabled_in_production()
    test_list_keys_never_exposes_plaintext_secret()
    test_ndcg_and_precision_bounded_to_1_even_with_duplicates()
    test_audit_ledger_hash_chain_integrity()
    test_atomic_supersede_query_locks_and_checks_active_status()
    test_no_raw_cypher_in_conflicts_or_graph_route_or_revoke_tool()
    print("ALL 6 P0 SAFETY-NET TESTS PASSED!")
