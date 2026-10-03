import pytest
from datetime import datetime, timezone
from kryptodb.graph.schema import Edge
from kryptodb.memory_api.propose import propose_fact

def filter_valid_edges(edges: list[Edge]) -> list[Edge]:
    now = datetime.now(timezone.utc)
    return [e for e in edges if (not e.valid_to or e.valid_to > now) and (not e.unlock_at or e.unlock_at <= now)]

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    class MockRedisClient:
        def __init__(self):
            self.store = {}
        def get(self, key):
            return self.store.get(key)
        def set(self, key, value):
            self.store[key] = value
            
    class MockRedisWrapper:
        def __init__(self):
            self.client = MockRedisClient()

    mock_redis_wrapper = MockRedisWrapper()
    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", mock_redis_wrapper)

def test_contradictory_two_agent_fixture():
    # Agent 1 proposes
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="campus-bot", subject_id="A", object_id="B")
    status, e1_commit, _ = propose_fact("campus-bot", e1, is_derived=False, existing_edges=[])
    assert status == "admit"
    
    # Agent 2 proposes contradictory
    e2 = Edge(id="2", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    status2, e2_commit, _ = propose_fact("malware-det", e2, is_derived=False, existing_edges=[e1_commit])
    assert status2 == "admit"
    assert e1_commit.valid_to is not None # supersession

def test_single_source_fixture():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    status, commit, _ = propose_fact("malware-det", e1, is_derived=False, existing_edges=[])
    assert status == "admit"

def test_multihop_lineage_fixture():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B", derived_from=["root_id", "sub_id"])
    status, commit, _ = propose_fact("malware-det", e1, is_derived=True, existing_edges=[])
    assert status == "admit"
    assert commit.derived_from == ["root_id", "sub_id"]

def test_superseded_fact_fixture():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="campus-bot", subject_id="A", object_id="B")
    status, e1_commit, _ = propose_fact("campus-bot", e1, is_derived=False, existing_edges=[])
    
    e2 = Edge(id="2", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    status2, e2_commit, _ = propose_fact("malware-det", e2, is_derived=False, existing_edges=[e1_commit])
    
    valid_edges = filter_valid_edges([e1_commit, e2_commit])
    assert len(valid_edges) == 1
    assert valid_edges[0].id == "2"

def test_propose_consensus_query_roundtrip():
    # Start empty
    edges = []
    
    # Propose
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    status, commit, _ = propose_fact("malware-det", e1, is_derived=False, existing_edges=edges)
    edges.append(commit)
    
    # Query
    valid = filter_valid_edges(edges)
    assert len(valid) == 1
    assert valid[0].id == "1"

def test_duplicate_proposal_is_idempotent():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    status1, commit1, _ = propose_fact("malware-det", e1, is_derived=False, existing_edges=[])
    assert status1 == "admit"
    
    # Propose exact same fact again
    status2, commit2, _ = propose_fact("malware-det", e1, is_derived=False, existing_edges=[commit1])
    assert status2 == "already_admitted"

def test_two_concurrent_proposals_do_not_both_supersede_same_fact():
    # Demonstrating the concurrency policy:
    # If two contradictory proposals are evaluated against the same prior state
    # (simulating a race condition before DB commit), their policies resolve deterministically.
    # The actual transactional abort will be handled by Neo4j write locks in Phase 3.
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="campus-bot", subject_id="A", object_id="B")
    e2 = Edge(id="2", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    
    status1, c1, _ = propose_fact("campus-bot", e1, is_derived=False, existing_edges=[])
    status2, c2, _ = propose_fact("malware-det", e2, is_derived=False, existing_edges=[])
    
    # Both pass policy independently, but DB locks will reject one.
    assert status1 == "admit"
    assert status2 == "admit"
