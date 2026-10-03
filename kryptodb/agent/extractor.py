"""
Phase 4: Structured Schema Extractor using Trustcall & Pydantic Validation.
Transforms raw conversation turns into structured, schema-validated FactItem objects.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, BaseMessage



class FactItem(BaseModel):
    """Structured Fact schema representation."""
    subject: str = Field(description="Subject entity identifier")
    predicate: str = Field(description="Relationship or predicate type")
    obj: str = Field(description="Target object entity or attribute value")
    scope: Optional[str] = Field(default="GLOBAL", description="Authorization/partition scope")
    description: Optional[str] = Field(default=None, description="Optional textual description")


class FactExtractionPatch(BaseModel):
    """Container schema for extracted candidate facts."""
    facts: List[FactItem] = Field(default_factory=list, description="List of extracted candidate facts")


def resolve_scope_for_content(content: str, agent_id: str = "agent-1", default_scope: str = "AUTO") -> str:
    """Intelligently infers the target knowledge graph partition scope from the content."""
    try:
        from kryptodb.memory_api.agents import get_agent
        agent = get_agent(agent_id)
        scopes = agent.get("allowed_scopes", []) if agent else []
    except Exception:
        scopes = []

    if not scopes:
        return default_scope if default_scope not in ("AUTO", "GLOBAL", "*") else "GLOBAL"

    if default_scope and default_scope not in ("AUTO", "GLOBAL", "*") and default_scope in scopes:
        return default_scope

    lower_c = content.lower()
    for s in scopes:
        clean_name = s.split(":")[-1].lower().replace("-", " ").replace("_", " ")
        parts = clean_name.split()
        if clean_name in lower_c or any(len(p) > 3 and p in lower_c for p in parts):
            return s

    return scopes[0] if scopes else "GLOBAL"


def extract_facts_from_messages(
    messages: List[Any],
    llm: Optional[Any] = None,
    agent_id: str = "agent-1",
    user_scope: str = "AUTO"
) -> List[Dict[str, Any]]:
    """
    Extracts structured FactItem objects from conversation messages.
    Uses native LangChain structured output when LLM is bound, or deterministic structured fallback.
    """
    if llm is not None and hasattr(llm, "with_structured_output"):
        try:
            structured_llm = llm.with_structured_output(FactExtractionPatch)
            res = structured_llm.invoke(messages)
            extracted = []
            facts = res.facts if isinstance(res, FactExtractionPatch) else (res.get("facts", []) if isinstance(res, dict) else [])
            for r in facts:
                dumped = r.model_dump() if hasattr(r, "model_dump") else (r if isinstance(r, dict) else {})
                if not dumped.get("scope") or dumped.get("scope") in ("AUTO", "GLOBAL"):
                    dumped["scope"] = resolve_scope_for_content(str(dumped), agent_id=agent_id, default_scope=user_scope)
                extracted.append(dumped)
            if extracted:
                return extracted
        except Exception:
            pass

    latest_query = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            latest_query = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            latest_query = m.get("content", "")
            break

    clean_query = latest_query.strip()
    lower_q = clean_query.lower()

    if lower_q.startswith(("store fact:", "fact:", "add fact:")):
        raw_fact = clean_query.split(":", 1)[1].strip()
        parts = [p.strip() for p in raw_fact.split(",") if p.strip()]
        if len(parts) == 3:
            subj, pred, obj = parts[0], parts[1], parts[2]
        elif " is the " in raw_fact.lower():
            idx = raw_fact.lower().find(" is the ")
            subj = raw_fact[:idx].strip()
            pred = "IS_THE"
            obj = raw_fact[idx + 8:].strip()
        elif " is " in raw_fact.lower():
            idx = raw_fact.lower().find(" is ")
            subj = raw_fact[:idx].strip()
            pred = "IS"
            obj = raw_fact[idx + 4:].strip()
        else:
            subj, pred, obj = agent_id, "STORED_FACT", raw_fact

        resolved_scope = resolve_scope_for_content(raw_fact, agent_id=agent_id, default_scope=user_scope)

        fact = FactItem(
            subject=subj,
            predicate=pred,
            obj=obj,
            scope=resolved_scope,
            description=raw_fact
        )
        return [fact.model_dump()]

    resolved_scope = resolve_scope_for_content(clean_query, agent_id=agent_id, default_scope=user_scope)
    fact = FactItem(
        subject=agent_id,
        predicate="PROCESSED_QUERY",
        obj=f"scope:{resolved_scope}",
        scope=resolved_scope,
        description=f"Extracted fact for query '{latest_query}'"
    )
    return [fact.model_dump()]

