"""
Minimal in-memory agent registry (Phase 2).
To be replaced by TOML config in Phase 3.
"""
from typing import Dict, Any

_AGENTS = {
    "malware-det": {"id": "malware-det", "trust_prior": 0.9, "allowed_scopes": ["RESEARCH:malware-detection"]},
    "campus-bot": {"id": "campus-bot", "trust_prior": 0.6, "allowed_scopes": ["PROJECT:campus-marketplace"]}
}

def get_agent(agent_id: str) -> Dict[str, Any] | None:
    return _AGENTS.get(agent_id)
