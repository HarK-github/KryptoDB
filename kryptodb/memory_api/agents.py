"""
Minimal agent registry.
Loads from data/agents.json if it exists.
"""
from typing import Dict, Any
import json
import os

_DEFAULT_AGENTS = {
    "admin": {"id": "admin", "trust_prior": 1.0, "allowed_scopes": ["*"]},
    "malware-det": {"id": "malware-det", "trust_prior": 0.9, "allowed_scopes": ["RESEARCH:malware-detection", "TEST"]},
    "campus-bot": {"id": "campus-bot", "trust_prior": 0.6, "allowed_scopes": ["PROJECT:campus-marketplace", "PROJECT:kryptodb", "PROJECT:synapse"]},
    "Agent-1": {"id": "Agent-1", "trust_prior": 0.9, "allowed_scopes": ["PROJECT:alpha", "SCOPE_ALPHA", "PROJECT:synapse", "PROJECT:kryptodb", "research", "ORG:acme-structure", "RESEARCH:threat-intel"]},
    "Agent-2": {"id": "Agent-2", "trust_prior": 0.9, "allowed_scopes": ["PROJECT:beta"]}
}

def get_agent(agent_id: str) -> Dict[str, Any] | None:
    agents = _DEFAULT_AGENTS.copy()
    
    # Override with JSON config if available
    json_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agents.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                disk_agents = json.load(f)
                agents.update(disk_agents)
        except Exception:
            pass
            
    if agent_id in agents:
        return agents[agent_id]
        
    lower_map = {k.lower(): v for k, v in agents.items()}
    return lower_map.get(agent_id.lower())
