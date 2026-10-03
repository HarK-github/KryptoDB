import pytest
from fastapi.testclient import TestClient
from kryptodb.daemon.server import app
from kryptodb.daemon.sessions import sessions
from kryptodb.daemon.keyring import keyring
from kryptodb.daemon.auth import api_keys


@pytest.fixture
def client():
    return TestClient(app)


def test_session_revocation_lifecycle(client, monkeypatch):
    """Verify that an admin can immediately revoke an active session and subsequent requests fail."""
    monkeypatch.setenv("KRYPTODB_ADMIN_API_KEY", "dev-admin-key")
    admin_auth = {"Authorization": "Bearer dev-admin-key"}

    # 1. Create an agent API key for existing Agent-1
    res_key = client.post(
        "/admin/api-keys",
        json={"agent_id": "Agent-1", "scopes": ["PROJECT:alpha"]},
        headers=admin_auth
    )
    assert res_key.status_code == 200
    agent_api_key = res_key.json()["api_key"]
    agent_auth = {"Authorization": f"Bearer {agent_api_key}"}

    # 2. Start a session
    res_sess = client.post(
        "/session",
        json={"active_partitions": ["PROJECT:alpha"]},
        headers=agent_auth
    )
    assert res_sess.status_code == 200
    session_id = res_sess.json()["session_id"]

    # Verify session is active
    assert sessions.get(session_id) is not None

    # 3. Revoke the session via admin endpoint
    res_revoke = client.post(
        f"/admin/sessions/{session_id}/revoke",
        headers=admin_auth
    )
    assert res_revoke.status_code == 200
    assert res_revoke.json()["status"] == "revoked"

    # 4. Subsequent queries using the revoked session must be rejected with 403
    res_query = client.post(
        "/memory/Agent-1/query",
        json={"scope": "PROJECT:alpha"},
        headers={
            "Authorization": f"Bearer {agent_api_key}",
            "X-KryptoDB-Session": session_id
        }
    )
    assert res_query.status_code == 403
    assert "Valid session activation required" in res_query.json()["detail"]


def test_session_revoke_nonexistent_returns_404(client, monkeypatch):
    monkeypatch.setenv("KRYPTODB_ADMIN_API_KEY", "dev-admin-key")
    admin_auth = {"Authorization": "Bearer dev-admin-key"}
    res = client.post("/admin/sessions/nonexistent_sess_123/revoke", headers=admin_auth)
    assert res.status_code == 404
