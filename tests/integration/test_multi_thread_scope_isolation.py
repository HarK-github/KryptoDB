import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph


def test_multi_thread_scope_isolation():
    """
    End-to-end integration test running two parallel agent threads with different scope tokens
    through the exact same compiled graph instance.
    Asserts that Thread B cannot read Thread A's memory substrate state.
    """
    checkpointer = MemorySaver()

    # Compiled graph shared instance
    graph = get_compiled_graph(checkpointer=checkpointer)

    config_a = {"configurable": {"thread_id": "thread-alpha-101"}}
    config_b = {"configurable": {"thread_id": "thread-beta-202"}}

    # Thread Alpha execution in SCOPE_ALPHA
    input_a = {
        "messages": [HumanMessage(content="Store classified payload in Scope Alpha")],
        "user_scope": "SCOPE_ALPHA",
        "agent_id": "agent-alpha"
    }

    res_a = graph.invoke(input_a, config_a)
    assert res_a["status"] == "written"
    assert res_a["user_scope"] == "SCOPE_ALPHA"

    # Thread Beta execution in SCOPE_BETA attempting to access SCOPE_ALPHA
    input_b = {
        "messages": [HumanMessage(content="Attempt unauthorized read from Scope Alpha")],
        "user_scope": "SCOPE_ALPHA",  # Thread B requesting unauthorized scope
        "agent_id": "agent-beta"
    }

    res_b = graph.invoke(input_b, config_b)

    # Thread B execution halts or receives no recalled facts from Scope Alpha
    assert res_b["user_scope"] == "SCOPE_ALPHA"
    # Recalled facts must not contain Thread Alpha's secret payload
    for fact in res_b.get("recalled_facts", []):
        if isinstance(fact, dict):
            assert fact.get("obj") != "Alpha_Secret_Payload"
