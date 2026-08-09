from fastmcp import FastMCP
import httpx
import os
import json

mcp = FastMCP("synapse")
DAEMON_URL = "http://localhost:8000"

def get_agent_id() -> str:
    """Resolve identity server-side (from environment in this stdio prototype)."""
    return os.environ.get("AGENT_ID", "Agent-1")

@mcp.tool()
def propose_fact_tool(subject: str, predicate: str, obj: str, scope: str) -> str:
    """Store a factual relationship in the encrypted memory substrate."""
    agent_id = get_agent_id()
    try:
        import uuid
        edge_data = {
            "id": str(uuid.uuid4()),
            "type": "FACT",
            "predicate": predicate,
            "permission_scope": scope,
            "source_agent_id": agent_id,
            "subject_id": subject,
            "object_id": obj
        }
        response = httpx.post(f"{DAEMON_URL}/memory", json={"agent_id": agent_id, "edge": edge_data})
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def task_status_tool(task_id: str) -> str:
    """Check the status of a deferred background task (like an async revocation cascade)."""
    try:
        response = httpx.get(f"{DAEMON_URL}/status/{task_id}")
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def run_onboard_tool(sample_facts: list[str]) -> str:
    """Auto-detect the appropriate schema and partition for a new agent based on sample facts."""
    agent_id = get_agent_id()
    try:
        response = httpx.post(f"{DAEMON_URL}/onboard", json={
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
    try:
        url = f"{DAEMON_URL}/memory/{agent_id}?"
        params = [f"mode={mode}"]
        if requested_scope:
            params.append(f"requested_scope={requested_scope}")
        if subject_id:
            params.append(f"subject_id={subject_id}")
        if query:
            params.append(f"query={query}")
            
        url += "&".join(params)
            
        response = httpx.get(url)
        if response.status_code == 403:
            return json.dumps({"status": "denied_permission", "facts": []})
        
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def session_show_tool() -> str:
    """Check which partitions are currently active/unlocked in this session."""
    try:
        response = httpx.get(f"{DAEMON_URL}/session")
        return json.dumps(response.json(), indent=2)
    except Exception as e:
        return f"error: {str(e)}"

@mcp.tool()
def reclassify_tool(node_id: str, to_scope: str) -> str:
    """Move a node to a different partition scope."""
    try:
        response = httpx.post(
            f"{DAEMON_URL}/reclassify",
            json={"node_id": node_id, "to_scope": to_scope}
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
