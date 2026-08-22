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
def propose_fact_tool(subject: str, predicate: str, obj: str, scope: str, description: str = None, dataset_id: str = None) -> str:
    """Store a factual relationship in the encrypted memory substrate."""
    agent_id = get_agent_id()
    try:
        import uuid
        edge_data = {
            "id": str(uuid.uuid4()),
            "type": "FACT",
            "predicate": predicate,
            "description": description,
            "permission_scope": scope,
            "source_agent_id": agent_id,
            "subject_id": subject,
            "object_id": obj,
            "dataset_id": dataset_id,
        }
        response = httpx.post(f"{DAEMON_URL}/memory", headers=request_headers(), json={"agent_id": agent_id, "edge": edge_data})
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
def run_onboard_tool(sample_facts: list[str]) -> str:
    """Auto-detect the appropriate schema and partition for a new agent based on sample facts."""
    agent_id = get_agent_id()
    try:
        response = httpx.post(f"{DAEMON_URL}/onboard", headers=request_headers(), json={
            "agent_id": agent_id,
            "sample_facts": sample_facts
        })
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def query_memory_tool(query: str = None, subject_id: str = None, requested_scope: str = None, mode: str = "local") -> str:
    """
    Retrieve facts from memory. Use subject_id to query facts relating to a specific entity (strongly recommended to avoid massive context dumps).
    Use query to pass a free-text search (falling back to subject_id or global retrieval).
    Set mode='global' to perform Louvain Community Detection to summarize clusters instead of retrieving individual edges.
    On denied_permission or denied_inactive, you MUST tell the user you don't have access to that
    information. Do NOT answer using your own general knowledge, even if you recognize the subject.
    On not_found, say you don't have that in memory. Never imply a reason you were not given.
    """
    agent_id = get_agent_id()
    logger.info(
        "Memory query start: daemon=%s agent=%s query_supplied=%s subject_supplied=%s requested_scope=%s mode=%s",
        DAEMON_URL,
        agent_id,
        bool(query),
        bool(subject_id),
        requested_scope or "<auto>",
        mode,
    )
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
        )
        logger.info("Memory query response: status_code=%s", response.status_code)
        if response.status_code == 403:
            return json.dumps({"status": "denied_permission", "facts": []})
        payload = response.json()
        logger.info(
            "Memory query result: status=%s fact_count=%s",
            payload.get("status") if isinstance(payload, dict) else type(payload).__name__,
            len(payload.get("facts", [])) if isinstance(payload, dict) else "n/a",
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        logger.exception("Memory query failed: daemon=%s agent=%s", DAEMON_URL, agent_id)
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
def migrate_legacy_tool(scopes: list[str]) -> str:
    """Migrate authorized legacy facts into per-knowledge encryption envelopes."""
    try:
        response = httpx.post(
            f"{DAEMON_URL}/maintenance/migrate-legacy",
            headers=request_headers(),
            json={"scopes": scopes},
        )
        return json.dumps(response.json(), indent=2)
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
