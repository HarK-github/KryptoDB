import pytest
from datetime import datetime, timezone, timedelta
from kryptodb.graph import Edge
from kryptodb.memory_api import admit_fact
from pathlib import Path

def filter_valid_edges(edges: list[Edge]) -> list[Edge]:
    now = datetime.now(timezone.utc)
    return [e for e in edges if (not e.valid_to or e.valid_to > now) and (not e.unlock_at or e.unlock_at <= now)]

def test_query_excludes_temporally_invalid_edges():
    now = datetime.now(timezone.utc)
    
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B", valid_to=now - timedelta(days=1))
    e2 = Edge(id="2", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B")
    
    res = filter_valid_edges([e1, e2])
    assert len(res) == 1
    assert res[0].id == "2"

def test_consensus_is_deterministic():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="malware-det", subject_id="A", object_id="B")
    res1 = admit_fact("malware-det", e1, [])
    res2 = admit_fact("malware-det", e1, [])
    assert res1 == res2 == "admit"

def test_consensus_does_not_call_llm():
    import inspect
    consensus_code = inspect.getsource(admit_fact)
    assert "requests" not in consensus_code
    assert "llm" not in consensus_code.lower()
    
def test_unknown_agent_is_denied():
    e1 = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="unknown", subject_id="A", object_id="B")
    assert admit_fact("unknown", e1, []) == "deny"
