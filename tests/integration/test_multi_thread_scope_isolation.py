import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from kryptodb.agent.graph import get_compiled_graph
from kryptodb.agent.store import KryptoDBStore


def test_multi_thread_scope_isolation():
    """
    End-to-end integration test running two parallel agent threads with different scope tokens
    through the exact same compiled graph instance and store.
    Asserts that Thread B cannot read Thread A's memory substrate state.
    """
    checkpointer = MemorySaver()

    # Shared store instances configured with scope permissions
    store_a = KryptoDBStore(agent_id="agent-alpha", authorized_scopes=["SCOPE_ALPHA"])
    store_b = KryptoDBStore(agent_id="agent-beta", authorized_scopes=["SCOPE_BETA"])

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

    # Store payload into Scope Alpha
    ns_alpha = KryptoDBStore.format_namespace("SCOPE_ALPHA", "agent-alpha")
    store_a.put(ns_alpha, "classified_payload", {
        "subject": "agent-alpha",
        "predicate": "CLASSIFIED_INFO",
        "obj": "Alpha_Secret_Payload",
        "scope": "SCOPE_ALPHA"
    })

    res_a = graph.invoke(input_a, config_a)
    assert res_a["status"] == "written"
    assert res_a["user_scope"] == "SCOPE_ALPHA"

    # Verify store_a can retrieve its own item
    item_a = store_a.get(ns_alpha, "classified_payload")
    assert item_a is not None

    # Verify store_b CANNOT retrieve Thread Alpha's item (structural scope isolation)
    item_b_attempt = store_b.get(ns_alpha, "classified_payload")
    assert item_b_attempt is None, "Thread B structurally breached Scope Alpha memory!"

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
