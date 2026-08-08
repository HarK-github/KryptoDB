"""
Trust lookup, conflict check, admission decision.
"""
from synapse.memory_api.agents import get_agent
from synapse.graph.schema import Edge

def admit_fact(agent_id: str, proposed_edge: Edge, existing_edges: list[Edge]) -> str:
    agent = get_agent(agent_id)
    if not agent:
        return "deny"
        
    trust_weight = agent["trust_prior"]
    proposed_edge.trust_weight = trust_weight
    
    if not existing_edges:
        if trust_weight >= 0.5:
            return "admit"
        else:
            return "reject"
            
    # Simple static arbitration: highest trust wins
    active_existing = [e for e in existing_edges if e.valid_to is None]
    max_existing_trust = max((e.trust_weight for e in active_existing), default=0.0)
    
    if trust_weight > max_existing_trust:
        return "admit"
    elif trust_weight == max_existing_trust:
        return "arbitrate"
    else:
        return "reject"
