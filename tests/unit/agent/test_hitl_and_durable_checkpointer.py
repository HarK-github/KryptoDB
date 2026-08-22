import pytest
import sqlite3
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from kryptodb.agent.graph import (
    get_compiled_graph,
    is_sensitive_scope,
)
from kryptodb.agent.store import KryptoDBStore


def test_is_sensitive_scope_detection():
    """Verify classification of sensitive memory scopes for HITL gating."""
    assert is_sensitive_scope("CONFIDENTIAL:financials") is True
    assert is_sensitive_scope("RESTRICTED:internal_docs") is True
    assert is_sensitive_scope("TOP_SECRET:intel") is True
    assert is_sensitive_scope("GLOBAL") is False
    assert is_sensitive_scope("RESEARCH:malware") is False


def test_hitl_write_approval_approved():
    """
    Test Phase 7 Human-In-The-Loop gating on sensitive scope write (Approved path).
    Graph pauses on interrupt, and resumes when approved=True is passed.
    """
    checkpointer = MemorySaver()
    store = KryptoDBStore(agent_id="hitl-agent", authorized_scopes=["TOP_SECRET:classified"])
    graph = get_compiled_graph(store=store, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "hitl-thread-approve-1"}}

    input_data = {
        "messages": [HumanMessage(content="Store classified defense intel")],
        "user_scope": "TOP_SECRET:classified",
        "agent_id": "hitl-agent"
    }

    # First execution pauses on interrupt before writing sensitive scope
    res1 = graph.invoke(input_data, config)

    # State snapshot shows graph paused on interrupt
    state = graph.get_state(config)
    assert len(state.tasks) > 0
    assert len(state.tasks[0].interrupts) > 0
    assert state.tasks[0].interrupts[0].value["reason"] == "high_sensitivity_write_approval_required"

    # Resume graph execution with approval
    res2 = graph.invoke(Command(resume={"approved": True}), config)

    assert res2["status"] == "written"


def test_hitl_write_approval_rejected():
    """
    Test Phase 7 Human-In-The-Loop gating on sensitive scope write (Rejected path).
    Graph pauses on interrupt, and halts write when approved=False is passed.
    """
    checkpointer = MemorySaver()
    store = KryptoDBStore(agent_id="hitl-agent", authorized_scopes=["CONFIDENTIAL:hr"])
    graph = get_compiled_graph(store=store, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "hitl-thread-reject-1"}}

    input_data = {
        "messages": [HumanMessage(content="Store confidential salary records")],
        "user_scope": "CONFIDENTIAL:hr",
        "agent_id": "hitl-agent"
    }

    # First execution pauses on interrupt
    res1 = graph.invoke(input_data, config)

    # Resume graph execution with rejection
    res2 = graph.invoke(Command(resume={"approved": False}), config)

    assert res2["status"] == "write_rejected"
    assert "Write Rejected" in res2["messages"][-1].content


def test_sqlite_durable_checkpointer():
    """Verify Phase 8 production durability using SqliteSaver checkpointer."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        checkpointer = SqliteSaver(conn)
        store = KryptoDBStore(agent_id="durable-agent", authorized_scopes=["GLOBAL"])
        graph = get_compiled_graph(store=store, checkpointer=checkpointer)

        config = {"configurable": {"thread_id": "durable-thread-1"}}

        input_data = {
            "messages": [HumanMessage(content="Persist durable state across restarts")],
            "user_scope": "GLOBAL",
            "agent_id": "durable-agent"
        }

        res = graph.invoke(input_data, config)
        assert res["status"] == "written"

        # Verify state was saved to SQLite checkpointer
        saved_state = graph.get_state(config)
        assert saved_state.values["user_scope"] == "GLOBAL"
        assert saved_state.values["status"] == "written"
    finally:
        conn.close()
