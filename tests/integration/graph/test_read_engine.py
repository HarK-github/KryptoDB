import pytest
from kryptodb.graph.read_engine import ReadEngine
from kryptodb.daemon.keyring import keyring

def test_read_engine_respects_activation_gate(monkeypatch):
    class MockNeo4jClient:
        def __init__(self):
            self.driver = self
        def execute_query(self, query, **kwargs):
            # Mock returning a record
            scopes = kwargs.get("scopes", [])
            if "PROJECT:alpha" in scopes:
                return ([{
                    "s": {"id": "Alice", "name_ct": "ciphertext"},
                    "o": {"id": "KryptoDB"},
                    "r": {
                        "id": "e1",
                        "predicate": "WORKS_ON",
                        "permission_scope": "PROJECT:alpha",
                        "trust_weight": 0.9,
                        "source_agent_id": "agent"
                    }
                }], None, None)
            return ([], None, None)
            
    monkeypatch.setattr("kryptodb.graph.read_engine.neo4j_client", MockNeo4jClient())
    
    # Mock Decryptor
    class MockEncryptor:
        def decrypt(self, scope, ct):
            return "Alice Plaintext" if scope == "PROJECT:alpha" else None
            
    monkeypatch.setattr("kryptodb.graph.read_engine.encryptor", MockEncryptor())
    
    engine = ReadEngine()
    
    # 1. Agent is allowed "PROJECT:alpha" but it is NOT active
    keyring.clear()
    res1 = engine.retrieve_active_facts(allowed_scopes=["PROJECT:alpha"])
    assert len(res1) == 0
    
    # 2. Agent is allowed AND it IS active
    keyring.add_key("PROJECT:alpha", b"12345678901234567890123456789012")
    res2 = engine.retrieve_active_facts(allowed_scopes=["PROJECT:alpha"])
    assert len(res2) == 1
    assert res2[0]["subject"] == "Alice Plaintext"

def test_read_engine_respects_permission_gate(monkeypatch):
    class MockNeo4jClient:
        def __init__(self):
            self.driver = self
        def execute_query(self, query, **kwargs):
            return ([], None, None)
            
    monkeypatch.setattr("kryptodb.graph.read_engine.neo4j_client", MockNeo4jClient())
    
    engine = ReadEngine()
    
    keyring.clear()
    keyring.add_key("PROJECT:alpha", b"key")
    
    # Agent is NOT allowed
    res = engine.retrieve_active_facts(allowed_scopes=["OTHER"])
    assert len(res) == 0

def test_read_engine_two_stage_retrieval(monkeypatch):
    class MockNeo4jClient:
        def __init__(self):
            self.driver = self
            self.last_query = None
        def execute_query(self, query, **kwargs):
            self.last_query = query
            return ([], None, None)
            
    mock_neo4j = MockNeo4jClient()
    monkeypatch.setattr("kryptodb.graph.read_engine.neo4j_client", mock_neo4j)
    
    class MockEmbeddingService:
        def encode(self, text):
            return [0.1, 0.2, 0.3]
            
    monkeypatch.setattr("kryptodb.graph.read_engine.embedding_service", MockEmbeddingService())
    
    engine = ReadEngine()
    keyring.clear()
    keyring.add_key("PROJECT:alpha", b"key")
    
    engine.retrieve_active_facts(allowed_scopes=["PROJECT:alpha"], query="test query")
    
    assert "search_indexes" in mock_neo4j.last_query
    assert "ANY(idx IN coalesce(r.search_indexes, [])" in mock_neo4j.last_query
