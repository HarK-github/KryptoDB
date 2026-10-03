import json
import logging
import pytest
from kryptodb.memory_api.service import memory_service
from kryptodb.daemon.auth import AuthContext
from kryptodb.daemon import keyring


def test_write_path_structured_logging_and_trace_propagation(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

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
    monkeypatch.setattr(neo4j_client, "execute_write_transaction", lambda work_func, **kwargs: work_func(MockTx()))

    trace_id = "trace-test-write-777"
    fact = {
        "id": "fact-obs-1",
        "subject": "jupiter",
        "predicate": "ORBITS",
        "object": "sun",
        "description": "Jupiter orbits the Sun."
    }

    res = memory_service.remember(
        agent_id="Agent-1",
        fact=fact,
        scope="research",
        context=AuthContext("Agent-1", "key-1", frozenset(["research"]), False),
        trace_id=trace_id
    )

    assert res["status"] in ("admit", "ok", "queued")
    assert res["trace_id"] == trace_id

    # Parse JSON structured log records
    parsed_logs = []
    for r in caplog.records:
        try:
            parsed_logs.append(json.loads(r.message))
        except Exception:
            pass

    write_stages = [
        item["stage"] for item in parsed_logs
        if item.get("event") == "stage_transition" and item.get("trace_id") == trace_id
    ]

    assert "validate" in write_stages
    assert "conflict_check" in write_stages
    assert "hitl" in write_stages
    assert "encrypt" in write_stages
    assert "commit" in write_stages


def test_read_path_structured_logging_and_trace_propagation(caplog, monkeypatch):
    caplog.set_level(logging.INFO)

    from kryptodb.storage.neo4j_client import neo4j_client
    monkeypatch.setattr(neo4j_client, "driver", object())
    monkeypatch.setattr(neo4j_client, "execute_query_with_retry", lambda query, **kwargs: ([], None, None))

    from kryptodb.daemon.sessions import sessions
    keyring.add_key("research", b"0" * 32)
    session = sessions.create("Agent-1", "key-1", set(["research"]))
    session.keys["research"] = b"0" * 32

    trace_id = "trace-test-read-888"
    res = memory_service.recall(
        agent_id="Agent-1",
        query="jupiter",
        scope="research",
        session_id=session.session_id,
        context=AuthContext("Agent-1", "key-1", frozenset(["research"]), False),
        trace_id=trace_id
    )

    assert res["trace_id"] == trace_id

    parsed_logs = []
    for r in caplog.records:
        try:
            parsed_logs.append(json.loads(r.message))
        except Exception:
            pass

    read_gates = [
        item["gate"] for item in parsed_logs
        if item.get("event") == "gate_transition" and item.get("trace_id") == trace_id
    ]

    assert "permission" in read_gates
    assert "activation" in read_gates
    assert "blind_index_match" in read_gates
    assert "decrypt" in read_gates
