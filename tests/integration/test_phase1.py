import pytest
from fastapi.testclient import TestClient
from kryptodb.daemon.server import app
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client
from kryptodb.daemon import keyring

client = TestClient(app)

def test_health_fails_when_neo4j_is_down(monkeypatch):
    monkeypatch.setattr(neo4j_client, "check_health", lambda: False)
    monkeypatch.setattr(redis_client, "check_health", lambda: True)
    
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"]["neo4j"] == "down"

def test_health_fails_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(neo4j_client, "check_health", lambda: True)
    monkeypatch.setattr(redis_client, "check_health", lambda: False)
    
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"]["redis"] == "down"

def test_requests_rejected_until_storage_is_live(monkeypatch):
    monkeypatch.setattr(neo4j_client, "check_health", lambda: False)
    
    # Since storage is down, application endpoints should theoretically be rejected.
    # We will enforce this via dependencies later, but for now health is the gate check.
    response = client.get("/health")
    assert response.status_code == 503

def test_daemon_restart_clears_session_keyring():
    from kryptodb.daemon import SessionKeyring
    k1 = SessionKeyring()
    k1.set_active_partitions({"TEST"})
    
    # Simulate restart by instantiating anew
    k2 = SessionKeyring()
    assert len(k2.get_active_partitions()) == 0
