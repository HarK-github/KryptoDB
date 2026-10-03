import pytest
from unittest.mock import MagicMock
from kryptodb.memory_api.service import memory_service
from kryptodb.daemon import event_bus
from kryptodb.daemon.auth import AuthContext


def test_remember_verb_proposes_and_commits(monkeypatch):
    class MockRecord:
        def __getitem__(self, key):
            if key == "s_id":
                return "s-uuid"
            if key == "o_id":
                return "o-uuid"
            return None

    class MockResult:
        def single(self):
            return MockRecord()

    class MockTx:
        def run(self, query, *args, **kwargs):
            return MockResult()

    from kryptodb.storage.neo4j_client import neo4j_client
    monkeypatch.setattr(neo4j_client, "driver", object())
    monkeypatch.setattr(neo4j_client, "execute_query_with_retry", lambda query, **kwargs: ([], None, None))
    monkeypatch.setattr(neo4j_client, "execute_write_transaction", lambda work_func: work_func(MockTx()))

    fact = {
        "id": "fact-verb-1",
        "subject": "apollo",
        "predicate": "DESTINATION",
        "object": "moon",
        "description": "Apollo mission targeted the Moon."
    }
    res = memory_service.remember(
        agent_id="Agent-1",
        fact=fact,
        scope="research",
        context=AuthContext("Agent-1", "key-1", frozenset(["research"]), False)
    )
    assert res["status"] in ("admit", "ok", "queued")
    assert res["edge"] is not None
    assert res["edge"]["id"] == "fact-verb-1"


def test_remember_sensitive_scope_requires_hitl():
    fact = {
        "id": "fact-sensitive-1",
        "subject": "target-x",
        "predicate": "CODENAME",
        "object": "black-briar"
    }
    # Without approved=True, must trigger HITL
    res = memory_service.remember(
        agent_id="admin",
        fact=fact,
        scope="CONFIDENTIAL:blackops",
        context=AuthContext("admin", "admin-key", frozenset(), True)
    )
    assert res["status"] == "hitl_required"
    assert "CONFIDENTIAL:blackops" in res["scope"]


def test_recall_verb_delegates_to_read_path():
    res = memory_service.recall(
        agent_id="Agent-1",
        query="apollo",
        scope="research",
        context=AuthContext("Agent-1", "key-1", frozenset(["research"]), False)
    )
    assert "status" in res
    assert "facts" in res


def test_forget_verb_retracts_and_purges_blind_indexes(monkeypatch):
    executed_queries = []
    
    class MockNeo4j:
        driver = object()
        def execute_query_with_retry(self, query, **params):
            executed_queries.append((query, params))
            return ([{"id": "fact-to-delete-1"}], None, None)

    mock_neo4j = MockNeo4j()
    monkeypatch.setattr("kryptodb.storage.neo4j_client.neo4j_client", mock_neo4j)

    res = memory_service.forget(
        agent_id="Agent-1",
        scope="research",
        fact_id="fact-to-delete-1",
        context=AuthContext("Agent-1", "key-1", frozenset(["research"]), False)
    )

    assert res["status"] == "forgotten"
    assert res["retracted_count"] == 1
    assert "fact-to-delete-1" in res["fact_ids"]

    # Verify that Cypher purged blind search indexes and stamped valid_to / revoked_at
    assert len(executed_queries) == 1
    cypher_text, params = executed_queries[0]
    assert "r.search_indexes = []" in cypher_text
    assert "r.subject_search_idx = NULL" in cypher_text
    assert "r.revocation_reason = 'forgotten'" in cypher_text
    assert params["fact_id"] == "fact-to-delete-1"


def test_forget_sensitive_scope_requires_hitl():
    res = memory_service.forget(
        agent_id="admin",
        scope="CONFIDENTIAL:intel",
        fact_id="fact-secret-9",
        approved=False,
        context=AuthContext("admin", "admin-key", frozenset(), True)
    )
    assert res["status"] == "hitl_required"
    assert res["scope"] == "CONFIDENTIAL:intel"


def test_forget_records_audit_ledger_entry_without_content():
    initial_audit_len = len(event_bus.audit_log)

    res = memory_service.forget(
        agent_id="Agent-1",
        scope="research",
        fact_id="fact-audited-77",
        context=AuthContext("Agent-1", "key-1", frozenset(["research"]), False)
    )
    assert res["status"] == "forgotten"

    # Verify latest audit log entry
    latest_entry = event_bus.audit_log[0]
    # Format: [timestamp, actor, event_type, scope, "allowed"]
    assert latest_entry[1] == "Agent-1"
    assert latest_entry[2] == "fact_forgotten"
    assert latest_entry[3] == "research"
    # Content must NOT be logged in the audit ledger
    assert "moon" not in str(latest_entry)
    assert "apollo" not in str(latest_entry)
