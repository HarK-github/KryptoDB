import pytest
from pydantic import ValidationError
from kryptodb.graph import Edge, Node, ContentFields
from kryptodb.graph.write_engine import WriteEngine
from datetime import datetime, timezone

def test_schema_requires_structural_fields():
    with pytest.raises(ValidationError):
        # Missing id
        Node(type="FACT", predicate="PERSON", permission_scope="TEST", name_ct="A", description_ct="B")

def test_content_fields_are_marked_for_encryption():
    assert "name_ct" in ContentFields.model_fields
    assert "description_ct" in ContentFields.model_fields

def test_primary_fact_has_empty_derived_from():
    edge = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B")
    engine = WriteEngine()
    engine.assert_lineage(edge, is_derived=False) # Should not raise
    
    with pytest.raises(ValueError):
        edge.derived_from = ["some_id"]
        engine.assert_lineage(edge, is_derived=False)

def test_derived_fact_requires_derived_from():
    edge = Edge(id="1", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B")
    engine = WriteEngine()
    
    with pytest.raises(ValueError):
        engine.assert_lineage(edge, is_derived=True)
        
    edge.derived_from = ["some_id"]
    engine.assert_lineage(edge, is_derived=True) # Should not raise

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

def test_supersession_closes_old_edge():
    old = Edge(id="old", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B", valid_from=datetime.now())
    new = Edge(id="new", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B")
    
    engine = WriteEngine()
    engine.write_fact(new, existing_edge=old)
    
    assert old.superseded_by == new.id
    assert old.valid_to is not None
    assert new.valid_to is None

def test_supersession_opens_new_edge():
    new = Edge(id="new", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="test", subject_id="A", object_id="B")
    
    engine = WriteEngine()
    engine.write_fact(new)
    
    assert new.valid_to is None
    assert new.valid_from is not None
