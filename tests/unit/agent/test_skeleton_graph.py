import pytest
from pydantic import ValidationError
from kryptodb.agent_runtime.langgraph.graph import (
    AgentState,
    FactItem,
    create_agent_graph,
    get_compiled_graph,
    create_recall_node,
    create_reasoning_node,
    create_extraction_node,
)


def test_agent_state_validation():
    """Verify state validation logic using Pydantic field validators."""
    # Valid state
    state = AgentState(user_scope="RESEARCH:malware", agent_id="agent-007")
    assert state.user_scope == "RESEARCH:malware"
    assert state.agent_id == "agent-007"

    # Empty user_scope should raise ValidationError
    with pytest.raises(ValidationError):
        AgentState(user_scope="")

    # Whitespace-only agent_id should raise ValidationError
    with pytest.raises(ValidationError):
        AgentState(agent_id="   ")


def test_individual_nodes():
    """Verify behavior of isolated node functions."""
    recall_node = create_recall_node()
    reasoning_node = create_reasoning_node()
    extraction_node = create_extraction_node()

    state = AgentState(
        messages=[{"role": "user", "content": "Analyze host 10.0.0.1"}],
        user_scope="NET_SEC",
        agent_id="agent-1"
    )

    # 1. recall_node
    recall_res = recall_node(state)
    assert recall_res["status"] == "recalling"
    assert len(recall_res["messages"]) == 1

    # 2. reasoning_node
    reasoning_res = reasoning_node(state)
    assert reasoning_res["status"] == "reasoned"

    # 3. extraction_node
    extract_res = extraction_node(state)
    assert extract_res["status"] in ("extracted", "conflict_detected")
    assert len(extract_res["pending_facts"]) > 0


def test_skeleton_graph_execution():
    """Verify end-to-end execution of Phase 1 skeleton graph through Phase 3 pipeline."""
    graph = get_compiled_graph()
    
    initial_input = {
        "messages": [{"role": "user", "content": "Check infected nodes"}],
        "user_scope": "INCIDENT_RESPONSE",
        "agent_id": "malware-detector"
    }

    result = graph.invoke(initial_input)

    # Assertions on pipeline output
    assert result["user_scope"] == "INCIDENT_RESPONSE"
    assert result["agent_id"] == "malware-detector"
    assert result["status"] in ("reasoned", "written")
    assert isinstance(result["recalled_facts"], list)
    assert result["reasoning_output"] is not None
    assert isinstance(result.get("pending_facts", []), list)
