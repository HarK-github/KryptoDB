from mcp.server import Server
from synapse.graph.schema import Edge
from synapse.memory_api.propose import propose_fact
from synapse.daemon.keyring import keyring

app = Server("synapse")

def propose_fact_tool(subject: str, predicate: str, obj: str, scope: str, agent_id: str) -> str:
    """Store a factual relationship in the encrypted memory substrate."""
    edge = Edge(
        id=f"{subject}-{predicate}-{obj}",
        type="FACT",
        permission_scope=scope,
        source_agent_id=agent_id,
        subject_id=subject,
        object_id=obj
    )
    existing_edges = []
    status, committed_edge = propose_fact(agent_id, edge, False, existing_edges)
    return status

def query_memory_tool(agent_id: str) -> str:
    """Retrieve all memory the agent has access to in active partitions."""
    active = list(keyring._keys.keys())
    return f"Access granted to active partitions: {active}"

# In a real startup script we would bind these tools to `app.list_tools` and `app.call_tool` handlers.
