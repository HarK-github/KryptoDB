import os
os.environ["KRYPTODB_ADMIN_API_KEY"] = "admin"

import pytest
from fastapi.testclient import TestClient
from kryptodb.daemon.server import app

client = TestClient(app)

def test_history_endpoint_returns_checkpointed_messages():
    thread_id = "test-history-thread-1"
    
    # 1. Post a message to create checkpoint state
    res = client.post(
        "/agent/chat",
        json={
            "thread_id": thread_id,
            "agent_id": "agent-1",
            "user_scope": "PROJECT:synapse",
            "message": "Hello graph"
        },
        headers={"Authorization": "Bearer admin"}
    )
    assert res.status_code == 200

    # 2. Query history
    res_hist = client.get(
        f"/chat/{thread_id}/history",
        headers={"Authorization": "Bearer admin"}
    )
    assert res_hist.status_code == 200
    data = res_hist.json()
    assert data["thread_id"] == thread_id
    assert len(data["messages"]) > 0
    assert data["messages"][0]["role"] == "user"
    assert "Hello graph" in data["messages"][0]["content"]


def test_message_endpoint_invokes_graph_with_correct_thread_id():
    thread_id = "test-msg-thread-2"
    res = client.post(
        "/agent/chat",
        json={
            "thread_id": thread_id,
            "agent_id": "agent-1",
            "user_scope": "PROJECT:synapse",
            "message": "Query penicillin allergies"
        },
        headers={"Authorization": "Bearer admin"}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["thread_id"] == thread_id
    assert "agent_response" in data


def test_approve_endpoint_resumes_graph():
    thread_id = "test-approve-thread-3"
    res_appr = client.post(
        "/agent/approve",
        json={"thread_id": thread_id, "approved": True},
        headers={"Authorization": "Bearer admin"}
    )
    assert res_appr.status_code == 200
    data = res_appr.json()
    assert data["approved"] is True


def test_reject_endpoint_resumes_graph():
    thread_id = "test-reject-thread-4"
    res_rej = client.post(
        "/agent/approve",
        json={"thread_id": thread_id, "approved": False},
        headers={"Authorization": "Bearer admin"}
    )
    assert res_rej.status_code == 200
    data = res_rej.json()
    assert data["approved"] is False
