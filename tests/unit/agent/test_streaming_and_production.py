import pytest
import json
import os
from langchain_core.messages import HumanMessage
from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph


@pytest.mark.asyncio
async def test_native_graph_astream():
    """Verify native LangGraph streaming events."""
    graph = get_compiled_graph()

    input_data = {
        "messages": [HumanMessage(content="Test streaming categorization")],
        "user_scope": "GLOBAL",
        "agent_id": "stream-agent"
    }

    events = []
    async for mode, payload in graph.astream(input_data, stream_mode=["messages", "updates"]):
        events.append((mode, payload))

    assert len(events) > 0


def test_langgraph_json_validity():
    """Verify langgraph.json exists and configures the graph entry point."""
    assert os.path.exists("langgraph.json")
    with open("langgraph.json", "r") as f:
        config = json.load(f)

    assert "graphs" in config
    assert "kryptodb_agent" in config["graphs"]
    assert config["graphs"]["kryptodb_agent"] == "./kryptodb/agent/graph.py:get_compiled_graph"
