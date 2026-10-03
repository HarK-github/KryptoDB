"""
KryptoDB FastMCP Server.
Designated primary read/write ingress interface for AI agents interacting
with the encrypted knowledge-graph memory substrate.
"""
from fastmcp import FastMCP
import httpx
import os
import json
import logging

mcp = FastMCP("kryptodb")
DAEMON_URL = os.environ.get("KRYPTODB_URL", "http://localhost:8000").rstrip("/")
logger = logging.getLogger("kryptodb.mcp")

def get_agent_id() -> str:
    """Resolve the display/URL identity; authorization comes from the API key."""
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
    return os.environ.get("KRYPTODB_API_KEY") or "dev-admin-key"


def request_headers() -> dict[str, str]:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("KRYPTODB_API_KEY is required")
    headers = {"Authorization": f"Bearer {api_key}"}
    session_id = os.environ.get("KRYPTODB_SESSION_ID")
    if session_id:
        headers["X-KryptoDB-Session"] = session_id
    return headers

@mcp.tool()
def remember(subject: str, predicate: str, obj: str, scope: str, description: str = None, dataset_id: str = None, session_id: str = None, trace_id: str = None) -> str:
    """Core Verb: remember(fact, scope, session_id?)
    Store a factual relationship in the encrypted memory substrate.
    """
    agent_id = get_agent_id()
    try:
        import uuid
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        logger.info(json.dumps({"event": "mcp_tool_invocation", "tool": "remember", "trace_id": trace_id, "agent_id": agent_id, "scope": scope}))
        headers = request_headers()
        headers["X-Trace-ID"] = trace_id
        if session_id:
            headers["X-KryptoDB-Session"] = session_id
        fact_payload = {
            "id": str(uuid.uuid4()),
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "description": description,
            "dataset_id": dataset_id,
        }
        response = httpx.post(
            f"{DAEMON_URL}/memory/remember",
            headers=headers,
            json={"agent_id": agent_id, "fact": fact_payload, "scope": scope, "session_id": session_id}
        )
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def propose_fact_tool(subject: str, predicate: str, obj: str, scope: str, description: str = None, dataset_id: str = None, trace_id: str = None) -> str:
    """Legacy alias for remember(). Stores a factual relationship in the encrypted memory substrate."""
    return remember(subject=subject, predicate=predicate, obj=obj, scope=scope, description=description, dataset_id=dataset_id, trace_id=trace_id)

@mcp.tool()
def recall(query: str = None, subject_id: str = None, requested_scope: str = None, session_id: str = None, mode: str = "local", trace_id: str = None) -> str:
    """Core Verb: recall(query, scope, session_id?)
    Retrieve facts from memory using 4-gate verification (Permission -> Activation -> Blind-Index -> Decrypt).
    On denied_permission or denied_inactive, informs the caller access is denied.
    """
    agent_id = get_agent_id()
    import uuid
    trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
    logger.info(json.dumps({"event": "mcp_tool_invocation", "tool": "recall", "trace_id": trace_id, "agent_id": agent_id, "scope": requested_scope}))
    headers = request_headers()
    headers["X-Trace-ID"] = trace_id
    if session_id:
        headers["X-KryptoDB-Session"] = session_id
    try:
        response = httpx.post(
            f"{DAEMON_URL}/memory/recall",
            headers=headers,
            json={
                "scope": requested_scope,
                "subject_id": subject_id,
                "query": query,
                "session_id": session_id,
            },
        )
        if response.status_code == 403:
            return json.dumps({"status": "denied_permission", "facts": [], "trace_id": trace_id})
        payload = response.json()
        return json.dumps(payload, indent=2)
    except Exception as e:
        logger.exception("Memory recall failed: daemon=%s agent=%s", DAEMON_URL, agent_id)
        return f"error: {str(e)}"

@mcp.tool()
def query_memory_tool(query: str = None, subject_id: str = None, requested_scope: str = None, mode: str = "local", trace_id: str = None) -> str:
    """Legacy alias for recall(). Retrieve facts from memory."""
    return recall(query=query, subject_id=subject_id, requested_scope=requested_scope, mode=mode, trace_id=trace_id)

@mcp.tool()
def forget(scope: str, fact_id: str = None, subject: str = None, predicate: str = None, approved: bool = False, trace_id: str = None) -> str:
    """Core Verb: forget(fact_id | subject+predicate, scope)
    Explicitly retract a fact from memory:
    - Purges blind-index entries and ciphertext on matching edges so terms become unsearchable.
    - Sensitive scopes require explicit HITL approval (approved=True).
    - Records an audit ledger entry (actor, scope, target, timestamp - not content).
    """
    try:
        import uuid
        trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        logger.info(json.dumps({"event": "mcp_tool_invocation", "tool": "forget", "trace_id": trace_id, "scope": scope}))
        headers = request_headers()
        headers["X-Trace-ID"] = trace_id
        response = httpx.post(
            f"{DAEMON_URL}/memory/forget",
            headers=headers,
            json={
                "scope": scope,
                "fact_id": fact_id,
                "subject": subject,
                "predicate": predicate,
                "approved": approved
            }
        )
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def task_status_tool(task_id: str) -> str:
    """Check the status of a deferred background task (like an async revocation cascade)."""
    try:
        response = httpx.get(f"{DAEMON_URL}/status/{task_id}", headers=request_headers())
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def session_show_tool() -> str:
    """Check which partitions are currently active/unlocked in this session."""
    try:
        response = httpx.get(f"{DAEMON_URL}/session", headers=request_headers())
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"


@mcp.tool()
def session_activate_tool(active_partitions: list[str]) -> str:
    """Activate authorized partitions for this MCP process session."""
    try:
        response = httpx.post(
            f"{DAEMON_URL}/session",
            headers=request_headers(),
            json={"active_partitions": active_partitions},
        )
        payload = response.json()
        if response.is_success and payload.get("session_id"):
            os.environ["KRYPTODB_SESSION_ID"] = payload["session_id"]
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def reclassify_tool(node_id: str = None, to_scope: str = "", knowledge_id: str = None) -> str:
    """Move a legacy node or one encrypted knowledge item to another scope."""
    try:
        response = httpx.post(
            f"{DAEMON_URL}/reclassify",
            headers=request_headers(),
            json={"node_id": node_id, "knowledge_id": knowledge_id, "to_scope": to_scope}
        )
        if response.status_code == 403:
            return json.dumps({"status": "error", "message": "Denied: Old partition must be active to decrypt."})
        elif response.status_code == 404:
            return json.dumps({"status": "error", "message": "Node not found."})
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport='stdio')
