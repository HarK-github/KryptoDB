import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from kryptodb.agent_runtime.langgraph.graph import (
    AgentState,
    create_agent_graph,
    get_compiled_graph,
    query_memory_tool,
    propose_fact_tool,
    KRYPTODB_TOOLS,
)


def test_kryptodb_tools_invocation():
    """Test direct tool function execution."""
    res_query = query_memory_tool.invoke({"subject_id": "test-agent", "requested_scope": "TEST_SCOPE"})
    assert "status" in res_query or "facts" in res_query

    res_propose = propose_fact_tool.invoke({
        "subject": "test-agent",
        "predicate": "TEST_PREDICATE",
        "obj": "TEST_OBJECT",
        "scope": "TEST_SCOPE"
    })
    assert "status" in res_propose or "edge_id" in res_propose


def test_tool_calling_graph_execution():
    """Verify ToolNode and tools_condition agentic loop in pipeline."""
    graph = get_compiled_graph()

    initial_input = {
        "messages": [HumanMessage(content="Store fact: Malware sample exhibits process injection in memory")],
        "user_scope": "RESEARCH:malware",
        "agent_id": "malware-detector"
    }

    result = graph.invoke(initial_input)

    # 1. Assert state pipeline output
    assert result["user_scope"] == "RESEARCH:malware"
    assert result["agent_id"] == "malware-detector"
    assert result["status"] == "written"

    # 2. Assert tool messages were generated and executed by ToolNode
    messages = result["messages"]
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) >= 2  # At least 1 for recall query and 1 for propose fact

    query_tool_msgs = [m for m in tool_messages if m.name == "query_memory_tool" or m.tool_call_id == "call_recall_query"]
    propose_tool_msgs = [m for m in tool_messages if m.name == "propose_fact_tool" or m.tool_call_id == "call_propose_fact"]

    assert len(query_tool_msgs) >= 1
    assert len(propose_tool_msgs) >= 1

    # 3. Assert recalled facts & pending facts derived from tool outputs
    assert len(result["recalled_facts"]) > 0
    assert len(result["pending_facts"]) > 0
