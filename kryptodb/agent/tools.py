"""
Tools for KryptoDB agentic graph workflows.
"""

from typing import Optional
import json
import os
import httpx
from langchain_core.tools import tool

DAEMON_URL = os.environ.get("KRYPTODB_URL", "http://localhost:8000").rstrip("/")


def get_agent_id() -> str:
    return os.environ.get("AGENT_ID", "Agent-1")


def get_api_key() -> str:
    key_file = os.environ.get("KRYPTODB_API_KEY_FILE")
    if key_file:
        try:
            with open(key_file, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return key
        except OSError:
            pass
    return os.environ.get("KRYPTODB_API_KEY", "dev-admin-key")


def request_headers() -> dict[str, str]:
    api_key = get_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    session_id = os.environ.get("KRYPTODB_SESSION_ID")
    if session_id:
        headers["X-KryptoDB-Session"] = session_id
    return headers


@tool
def query_memory_tool(
    query: Optional[str] = None,
    subject_id: Optional[str] = None,
    requested_scope: Optional[str] = None,
    mode: str = "local"
) -> str:
    """Retrieve facts from encrypted memory substrate.
    
    Use subject_id to query facts relating to a specific entity.
    Use query for free-text search.
    On denied_permission or denied_inactive, returned status indicates denied access.
    """
    agent_id = get_agent_id()
    try:
        response = httpx.post(
            f"{DAEMON_URL}/memory/{agent_id}/query",
            headers=request_headers(),
            json={
                "mode": mode,
                "requested_scope": requested_scope,
                "subject_id": subject_id,
                "query": query,
            },
            timeout=10.0
        )
        if response.status_code == 403:
            return json.dumps({"status": "denied_permission", "facts": []})
        elif response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            return json.dumps({"status": "not_found", "facts": []})
    except Exception as exc:
        # Do not fabricate a fact when the memory service is unavailable. An
        # unavailable graph must be distinguishable from a successful recall.
        return json.dumps({"status": "unavailable", "facts": [], "error": str(exc)})


@tool
def propose_fact_tool(
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    obj: Optional[str] = None,
    scope: Optional[str] = None,
    description: Optional[str] = None,
    dataset_id: Optional[str] = None
) -> str:
    """Store a factual relationship in the encrypted memory substrate.
    
    Args:
        subject: The subject entity (e.g. 'Alice', 'Project Synapse'). Defaults to current agent ID.
        predicate: The relationship predicate (e.g. 'IS_LEAD', 'HAS_CONTACT'). Defaults to HAS_FACT.
        obj: The object entity or value (e.g. '555-0199', 'Neo4j'). Defaults to value.
        scope: Permission scope (e.g. 'research', 'GLOBAL', 'CONFIDENTIAL:keys'). Defaults to research.
        description: Description of the fact.
    """
    agent_id = get_agent_id()
    subj_val = subject or agent_id
    pred_val = predicate or "HAS_FACT"
    obj_val = obj or "value"
    scope_val = scope or "research"
    try:
        import uuid
        edge_data = {
            "id": str(uuid.uuid4()),
            "type": "FACT",
            "predicate": pred_val,
            "description": description or f"Fact {subj_val} {pred_val} {obj_val}",
            "permission_scope": scope_val,
            "source_agent_id": agent_id,
            "subject_id": subj_val,
            "object_id": obj_val,
            "dataset_id": dataset_id,
        }
        response = httpx.post(
            f"{DAEMON_URL}/memory",
            headers=request_headers(),
            json={"agent_id": agent_id, "edge": edge_data},
            timeout=10.0
        )
        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            return json.dumps({"status": "rejected", "code": response.status_code})
    except Exception as e:
        return json.dumps({"status": "failed", "reason": str(e)})


KRYPTODB_TOOLS = [query_memory_tool, propose_fact_tool]
