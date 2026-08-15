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

def test_bootstrap_is_idempotent():
    from kryptodb.storage.bootstrap import bootstrap_storage
    import kryptodb.storage.bootstrap as bs
    
    bs.neo4j_client = MagicMock()
    bs.redis_client = MagicMock()
    
    # Run twice
    bootstrap_storage()
    bootstrap_storage()
    
    assert bs.neo4j_client.connect.call_count == 2
    assert bs.redis_client.connect.call_count == 2
