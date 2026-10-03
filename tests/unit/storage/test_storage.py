import pytest
from unittest.mock import MagicMock
from kryptodb.storage.neo4j_client import Neo4jClient
from kryptodb.storage.redis_client import RedisClient

def test_neo4j_connection_health():
    client = Neo4jClient()
    client.driver = MagicMock()
    
    # Healthy
    assert client.check_health() is True
    
    # Unhealthy
    client.driver.verify_connectivity.side_effect = Exception("Down")
    assert client.check_health() is False

def test_redis_connection_health():
    client = RedisClient()
    client.client = MagicMock()
    
    # Healthy
    client.client.ping.return_value = True
    assert client.check_health() is True
    
    # Unhealthy
    client.client.ping.side_effect = Exception("Down")
    assert client.check_health() is False

def test_redis_keyspaces_are_separate():
    """permission-bitmap keys and partition-key entries use distinct prefixes/keyspaces."""
    # Placeholder for Phase 3 keyspace validation
    pass

def test_bootstrap_is_idempotent(monkeypatch):
    from kryptodb.storage import bootstrap_storage
    import kryptodb.storage.neo4j_client as n4j
    import kryptodb.storage.redis_client as red
    
    mock_n4j = MagicMock()
    mock_red = MagicMock()
    monkeypatch.setattr(n4j, "neo4j_client", mock_n4j)
    monkeypatch.setattr(red, "redis_client", mock_red)
    
    # Run twice
    bootstrap_storage()
    bootstrap_storage()
    
    assert mock_n4j.connect.call_count == 2
    assert mock_red.connect.call_count == 2

