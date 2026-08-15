import pytest
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from kryptodb.storage.archiver import ColdStorageArchiver
from kryptodb.graph.schema import Edge
from kryptodb.graph.write_engine import WriteEngine
from kryptodb.storage.neo4j_client import neo4j_client

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

    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", MockRedisWrapper())

def test_archiver_moves_old_edges_to_sqlite():
    if not neo4j_client.driver:
        pytest.skip("Neo4j not connected")
        
    db_path = "data/test_cold_storage.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    # Write a fact and supersede it immediately
    engine = WriteEngine()
    old = Edge(id="test-old", type="FACT", predicate="KNOWS", permission_scope="TEST", source_agent_id="agent1", subject_id="A", object_id="B")
    engine.write_fact(old)
    
    # Manually backdate the valid_to so it qualifies as > 30 days old
    old_valid_to = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    neo4j_client.driver.execute_query(
        "MATCH ()-[r:FACT {id: 'test-old'}]->() SET r.valid_to = $vt, r.superseded_by = 'test-new'", 
        vt=old_valid_to
    )
    
    archiver = ColdStorageArchiver(db_path=db_path)
    count = archiver.run_sweep(days_old=30)
    
    assert count >= 1
    
    # Verify in SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, valid_to, superseded_by FROM archived_edges WHERE id = 'test-old'")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "test-old"
    assert row[2] == "test-new"
    
    # Verify removed from Neo4j
    res, _, _ = neo4j_client.driver.execute_query("MATCH ()-[r:FACT {id: 'test-old'}]->() RETURN r")
    assert len(res) == 0
    
    conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)
