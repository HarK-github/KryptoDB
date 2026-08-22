"""
Phase 6: Real-time Streaming Event Handler for KryptoDB Agent Graph.
Wraps LangGraph astream_events to provide real-time categorised event streams.
"""

from typing import AsyncGenerator, Dict, Any, Optional


async def stream_graph_events(
    graph: Any,
    input_data: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Asynchronously streams graph execution events, categorizing nodes and tool invocations
    into real-time event types: recall_event, reasoning_event, extraction_event, write_event, denial_event.
    """
    async for event in graph.astream_events(input_data, config=config, version="v2"):
        event_name = event.get("name", "")
        event_type = event.get("event", "")

        # Categorize node start/end and tool executions
        category = "general_event"
        if event_name in ("recall_node", "query_memory_tool"):
            category = "recall_event"
        elif event_name in ("reasoning_node", "recall_eval"):
            category = "reasoning_event"
        elif event_name in ("extraction_node", "conflict_resolution_node", "extraction_subgraph"):
            category = "extraction_event"
        elif event_name in ("direct_write_node", "propose_fact_tool"):
            category = "write_event"
        elif event_name == "denied_handling":
            category = "denial_event"

        yield {
            "event_type": category,
            "raw_event": event_type,
            "node_or_tool": event_name,
            "data": event.get("data", {})
        }
