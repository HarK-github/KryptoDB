"""
Agent Onboarding & Schema Auto-Detection.
Implements Milestone 9.5: LLM-based inference of partition scopes and ontology schemas.
"""
import json
import os
import re

def infer_schema(sample_facts: list[str]) -> tuple[str, list[str]]:
    """
    Mock LLM inference. In production, this would call an LLM (e.g., via OpenAI API)
    with a prompt to extract the domain/scope and common predicates.
    For this prototype, we use simple keyword matching to simulate schema auto-detection.
    """
    text = " ".join(sample_facts).lower()
    
    # Heuristics for scope
    scope = "GLOBAL:default"
    if any(k in text for k in ["bitcoin", "crypto", "blockchain", "wallet"]):
        scope = "FINANCE:crypto"
    elif any(k in text for k in ["malware", "virus", "exploit", "cve"]):
        scope = "RESEARCH:cybersecurity"
    elif any(k in text for k in ["university", "campus", "student", "course"]):
        scope = "PROJECT:campus-operations"
        
    # Heuristics for predicates
    predicates = []
    if "buy" in text or "owns" in text:
        predicates.append("OWNS")
    if "attack" in text or "infect" in text:
        predicates.append("INFECTED")
    if "study" in text or "enrolled" in text:
        predicates.append("ENROLLED_IN")
        
    if not predicates:
        predicates = ["RELATED_TO"]
        
    return scope, predicates

def auto_onboard_agent(agent_id: str, sample_facts: list[str], trust_prior: float = 0.5) -> dict:
    scope, predicates = infer_schema(sample_facts)
    
    # 1. Update agents.json
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    os.makedirs(base_dir, exist_ok=True)
    
    agents_path = os.path.join(base_dir, "agents.json")
    agents = {}
    if os.path.exists(agents_path):
        try:
            with open(agents_path, "r") as f:
                agents = json.load(f)
        except Exception:
            pass
            
    agents[agent_id] = {
        "id": agent_id,
        "trust_prior": trust_prior,
        "allowed_scopes": [scope]
    }
    
    with open(agents_path, "w") as f:
        json.dump(agents, f, indent=2)
        
    # 2. Update ontology.toml
    ontology_path = os.path.join(base_dir, "ontology.toml")
    ontology_content = f"""
# Auto-generated Ontology Schema for {agent_id}

[scopes.{scope.replace(':', '_')}]
name = "{scope}"
description = "Auto-inferred partition from agent {agent_id}"

[predicates]
"""
    for pred in predicates:
        ontology_content += f'{pred} = "Auto-inferred predicate"\n'
        
    with open(ontology_path, "a" if os.path.exists(ontology_path) else "w") as f:
        f.write(ontology_content)
        
    return {
        "agent_id": agent_id,
        "inferred_scope": scope,
        "inferred_predicates": predicates,
        "status": "onboarded"
    }
