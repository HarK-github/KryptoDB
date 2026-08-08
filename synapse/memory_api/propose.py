"""
Agent -> candidate fact submission endpoint.
"""
from synapse.graph.schema import Edge
from synapse.memory_api.consensus import admit_fact
from synapse.graph.write_engine import WriteEngine

write_engine = WriteEngine()

def propose_fact(agent_id: str, edge: Edge, is_derived: bool, existing_edges: list[Edge]) -> tuple[str, Edge | None]:
    try:
        write_engine.assert_lineage(edge, is_derived)
    except ValueError as e:
        return f"error: {str(e)}", None
        
    # Idempotency check
    for e in existing_edges:
        if e.id == edge.id:
            return "already_admitted", e
            
    decision = admit_fact(agent_id, edge, existing_edges)
    
    if decision == "admit":
        existing_active = next((e for e in existing_edges if e.valid_to is None), None)
        committed_edge = write_engine.write_fact(edge, existing_active)
        return "admit", committed_edge
    
    return decision, None
