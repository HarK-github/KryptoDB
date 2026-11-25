import pytest
from langchain_core.messages import HumanMessage
from kryptodb.agent_runtime.langgraph.graph import (
    extract_facts_from_messages,
    FactItem,
    get_compiled_graph,
    AgentState,
)


def test_structured_fact_extraction():
    """Verify structured fact extraction."""
    messages = [HumanMessage(content="Host 10.0.0.5 was infected with Trojan.AA")]
    facts = extract_facts_from_messages(
        messages=messages,
        agent_id="malware-detector",
        user_scope="RESEARCH:malware"
    )

    assert len(facts) > 0
    first_fact = facts[0]
    assert "subject" in first_fact
    assert "predicate" in first_fact
    assert "obj" in first_fact
    assert first_fact["scope"] == "RESEARCH:malware"


def test_graph_execution():
    """Verify end-to-end graph execution without custom store."""
    graph = get_compiled_graph()

    initial_state = {
        "messages": [HumanMessage(content="Scan active infected hosts")],
        "user_scope": "RESEARCH:malware",
        "agent_id": "malware-detector"
    }

    result = graph.invoke(initial_state)

    assert result["status"] == "written"
    assert result["user_scope"] == "RESEARCH:malware"
    assert len(result["pending_facts"]) > 0
