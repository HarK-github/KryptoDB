"""
Agent -> gated read endpoint.
"""
from datetime import datetime, timezone
from synapse.graph.schema import Edge

def filter_valid_edges(edges: list[Edge]) -> list[Edge]:
    now = datetime.now(timezone.utc)
    valid_edges = []
    for edge in edges:
        if edge.valid_to and edge.valid_to <= now:
            continue
        if edge.unlock_at and edge.unlock_at > now:
            continue
        valid_edges.append(edge)
    return valid_edges
