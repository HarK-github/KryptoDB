import pytest
import json
import os
from langchain_core.messages import HumanMessage
from kryptodb.agent.graph import get_compiled_graph
from kryptodb.agent.store import KryptoDBStore
from kryptodb.agent.streaming import stream_graph_events


@pytest.mark.asyncio
async def test_stream_graph_events_categorization():
    """Verify real-time categorised streaming events via stream_graph_events."""
    store = KryptoDBStore(agent_id="stream-agent", authorized_scopes=["GLOBAL"])
    graph = get_compiled_graph(store=store)

    input_data = {
        "messages": [HumanMessage(content="Test streaming categorization")],
        "user_scope": "GLOBAL",
        "agent_id": "stream-agent"
    }

    events = []
    async for evt in stream_graph_events(graph, input_data):
        events.append(evt)

    assert len(events) > 0
    categories = {e["event_type"] for e in events}
    # Verify categorized event types are captured
    assert categories.intersection({"recall_event", "reasoning_event", "extraction_event", "write_event", "general_event"})


def test_langgraph_json_validity():
    """Verify langgraph.json exists and configures the graph entry point."""
    assert os.path.exists("langgraph.json")
    with open("langgraph.json", "r") as f:
        config = json.load(f)

    assert "graphs" in config
    assert "kryptodb_agent" in config["graphs"]
    assert config["graphs"]["kryptodb_agent"] == "./kryptodb/agent/graph.py:get_compiled_graph"
