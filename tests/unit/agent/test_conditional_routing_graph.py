import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from kryptodb.agent.graph import (
    AgentState,
    create_agent_graph,
    get_compiled_graph,
)


def test_access_denial_gating():
    """Verify routing to denied_handling node on 403 / denied_permission status."""
    graph = get_compiled_graph()

    # Pre-populate state with access denial
    initial_state = {
        "messages": [HumanMessage(content="Access top secret partition")],
        "user_scope": "TOP_SECRET:restricted",
        "agent_id": "agent-007",
        "denied_recalls": [{"status": "denied_permission", "scope": "TOP_SECRET:restricted"}]
    }

    result = graph.invoke(initial_state)

    # Must route to denied_handling and halt execution
    assert result["status"] == "denied"
    assert result.get("reasoning_output") is None
    
    # Last message must explicitly report access denial
    last_msg = result["messages"][-1]
    assert isinstance(last_msg, AIMessage)
    assert "Access Denied" in last_msg.content


def test_conflict_resolution_gating():
    """Verify routing to conflict_resolution_node on Subject+Predicate collision."""
    graph = get_compiled_graph()

    # Pre-populate recalled_facts with an existing fact matching subject & predicate
    recalled = [{
        "subject": "malware-detector",
        "predicate": "PROCESSED_QUERY",
        "object": "scope:GLOBAL",
        "scope": "GLOBAL"
    }]

    initial_state = {
        "messages": [HumanMessage(content="Update active status")],
        "user_scope": "GLOBAL",
        "agent_id": "malware-detector",
        "recalled_facts": recalled
    }

    result = graph.invoke(initial_state)

    # Must detect collision and route through conflict_resolution_node
    assert result["status"] in ("written", "conflict_resolved")
    assert len(result.get("conflicting_facts", [])) > 0
    assert len(result["pending_facts"]) > 0


def test_normal_gated_flow():
    """Verify end-to-end execution without access denials or conflicts."""
    graph = get_compiled_graph()

    initial_state = {
        "messages": [HumanMessage(content="Routine health query")],
        "user_scope": "PUBLIC:general",
        "agent_id": "agent-1"
    }

    result = graph.invoke(initial_state)

    assert result["status"] == "written"
    assert len(result.get("denied_recalls", [])) == 0
    assert len(result.get("conflicting_facts", [])) == 0
    assert result["reasoning_output"] is not None
