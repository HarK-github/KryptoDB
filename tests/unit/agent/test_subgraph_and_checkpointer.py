import pytest
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver

from kryptodb.agent.graph import (
    AgentState,
    create_extraction_subgraph,
    get_compiled_graph,
)
from kryptodb.agent.store import KryptoDBStore


def test_extraction_subgraph_isolated():
    """Verify isolated execution of extraction & write subgraph."""
    subgraph = create_extraction_subgraph()

    initial_input = {
        "messages": [HumanMessage(content="Identify threat malware pattern")],
        "user_scope": "RESEARCH:malware",
        "agent_id": "malware-detector"
    }

    result = subgraph.invoke(initial_input)

    assert result["status"] == "written"
    assert len(result["pending_facts"]) > 0


def test_mid_conversation_scope_revocation_time_travel():
    """
    Simulate mid-conversation scope revocation using checkpointer state updates.
    Asserts that on scope revocation mid-thread, the pipeline gracefully routes to denied_handling.
    """
    checkpointer = MemorySaver()
    store = KryptoDBStore(agent_id="malware-detector", authorized_scopes=["RESEARCH:malware"])
    graph = get_compiled_graph(store=store, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "session-101"}}

    # Turn 1: Authorized scope execution
    input_step1 = {
        "messages": [HumanMessage(content="Retrieve malware analysis context")],
        "user_scope": "RESEARCH:malware",
        "agent_id": "malware-detector"
    }

    res1 = graph.invoke(input_step1, config)
    assert res1["status"] == "written"
    assert res1["user_scope"] == "RESEARCH:malware"

    # Turn 2: Simulate scope revocation mid-conversation via checkpointer update_state
    graph.update_state(
        config,
        {
            "user_scope": "RESTRICTED:revoked",
            "denied_recalls": [{"status": "denied_permission", "scope": "RESTRICTED:revoked"}]
        },
        as_node="recall_node"
    )

    # Re-invoke thread
    res2 = graph.invoke(None, config)

    # Must route to denied_handling node and halt
    assert res2["status"] == "denied"
    last_msg = res2["messages"][-1]
    assert isinstance(last_msg, AIMessage)
    assert "Access Denied" in last_msg.content
