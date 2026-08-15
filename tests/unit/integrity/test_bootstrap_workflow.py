import json

import pytest

from kryptodb.graph.schema import Edge
from kryptodb.graph.write_engine import WriteEngine


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, *args, **kwargs):
        self.store[key] = value


class FakeRedisWrapper:
    def __init__(self):
        self.client = FakeRedis()


class FakeNeo4j:
    driver = object()

    def __init__(self):
        self.params = None

    def execute_query_with_retry(self, query, **params):
        self.params = params
        return ([{"s_id": "subject-node", "o_id": "object-node"}], None, None)


def test_bootstrap_write_stages_plaintext_until_key_is_configured(monkeypatch):
    monkeypatch.setenv("KRYPTODB_ENV", "production")
    monkeypatch.setenv("KRYPTODB_MASTER_KEY", "00" * 32)
    monkeypatch.setenv("KRYPTODB_BOOTSTRAP_UNENCRYPTED", "true")
    monkeypatch.delenv("KRYPTODB_SCOPE_KEYS_JSON", raising=False)
    monkeypatch.delenv("KRYPTODB_SCOPE_KEYS_FILE", raising=False)

    redis = FakeRedisWrapper()
    neo4j = FakeNeo4j()
    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", redis)
    monkeypatch.setattr("kryptodb.integrity.bootstrap_state.redis_client", redis)
    monkeypatch.setattr("kryptodb.storage.neo4j_client.neo4j_client", neo4j)

    edge = Edge(
        id="pending-1", type="FACT", predicate="HAS", permission_scope="diagnosis",
        source_agent_id="Agent-1", subject_id="patient-1", object_id="condition-1",
        dataset_id="diagnosis-dataset",
    )
    WriteEngine().write_fact(edge)

    assert neo4j.params["encryption_status"] == "pending"
    assert neo4j.params["knowledge_ct"] is None
    pending = json.loads(neo4j.params["pending_envelope"])
    assert pending["subject"] == "patient-1"
    assert pending["dataset_id"] == "diagnosis-dataset"


def test_bootstrap_lock_blocks_new_unencrypted_writes(monkeypatch):
    monkeypatch.setenv("KRYPTODB_ENV", "production")
    monkeypatch.setenv("KRYPTODB_MASTER_KEY", "00" * 32)
    monkeypatch.setenv("KRYPTODB_BOOTSTRAP_UNENCRYPTED", "true")
    monkeypatch.delenv("KRYPTODB_SCOPE_KEYS_JSON", raising=False)
    monkeypatch.delenv("KRYPTODB_SCOPE_KEYS_FILE", raising=False)

    redis = FakeRedisWrapper()
    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", redis)
    monkeypatch.setattr("kryptodb.integrity.bootstrap_state.redis_client", redis)
    from kryptodb.integrity.bootstrap_state import lock
    lock()

    edge = Edge(
        id="blocked-1", type="FACT", predicate="HAS", permission_scope="diagnosis",
        source_agent_id="Agent-1", subject_id="patient-1", object_id="condition-1",
    )
    with pytest.raises(RuntimeError, match="Bootstrap mode is not enabled"):
        WriteEngine().write_fact(edge)
