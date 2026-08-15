"""
Agent -> candidate fact submission endpoint.
"""
from kryptodb.graph.schema import Edge
from kryptodb.memory_api.consensus import admit_fact
from kryptodb.graph.write_engine import WriteEngine

write_engine = WriteEngine()

def propose_fact(agent_id: str, edge: Edge, is_derived: bool, existing_edges: list[Edge]) -> tuple[str, Edge | None, dict | None]:
    try:
        write_engine.assert_lineage(edge, is_derived)
    except ValueError as e:
        return f"error: {str(e)}", None, None
        
    # Idempotency check
    for e in existing_edges:
        if e.id == edge.id:
            return "already_admitted", e, None
            
    decision = admit_fact(agent_id, edge, existing_edges)
    
    if decision == "admit":
        existing_active = next((e for e in existing_edges if e.valid_to is None), None)
        committed_edge, cascade_args = write_engine.write_fact(edge, existing_active)
        return "admit", committed_edge, cascade_args
    
    return decision, None, None
