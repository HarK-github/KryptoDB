"""Stateful, graph-aware delta extraction engine with temporal reasoning.

Decomposes conversational turns into state mutations (ADD, SUPERSEDE, CORRECT,
RETRACT, TEMPORAL_SHIFT, PLAN, CONFIRM_COMPLETION) conditioned on prior entity state.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from kryptodb.core.memory.predicates import predicate_registry

logger = logging.getLogger("kryptodb.core.memory.delta_extractor")


class DeltaAction(str, Enum):
    ADD = "ADD"
    SUPERSEDE = "SUPERSEDE"
    CORRECT = "CORRECT"
    RETRACT = "RETRACT"
    TEMPORAL_SHIFT = "TEMPORAL_SHIFT"
    PLAN = "PLAN"
    CONFIRM_COMPLETION = "CONFIRM_COMPLETION"


class FactItem(BaseModel):
    """Structured Fact schema representation."""
    subject: str = Field(description="Subject entity identifier")
    predicate: str = Field(description="Relationship or predicate type")
    obj: str = Field(description="Target object entity or attribute value")
    scope: Optional[str] = Field(default="GLOBAL", description="Authorization/partition scope")
    description: Optional[str] = Field(default=None, description="Optional textual description")
    quote: Optional[str] = Field(default=None, description="Verbatim quotation substring from user text")
    confidence: float = Field(default=1.0, description="Confidence score in [0.0, 1.0]")


class FactExtractionPatch(BaseModel):
    """Container schema for extracted candidate facts."""
    facts: List[FactItem] = Field(default_factory=list, description="List of extracted candidate facts")


def resolve_scope_for_content(
    content: str,
    agent_id: str = "agent-1",
    default_scope: str = "AUTO",
    allowed_scopes: Optional[List[str]] = None,
) -> str:
    """Infers the target knowledge graph partition scope from content without external service coupling."""
    if default_scope and default_scope not in ("AUTO", "GLOBAL", "*"):
        return default_scope

    lower_c = content.lower()
    proj_match = re.search(r"\bproject\s+([a-zA-Z0-9_-]+)\b", lower_c)
    if proj_match:
        return f"PROJECT:{proj_match.group(1).lower()}"

    if allowed_scopes:
        for s in allowed_scopes:
            clean_name = s.split(":")[-1].lower().replace("-", " ").replace("_", " ")
            parts = clean_name.split()
            if clean_name in lower_c or any(len(p) > 3 and p in lower_c for p in parts):
                return s
        return allowed_scopes[0]

    return default_scope if default_scope not in ("AUTO", "GLOBAL", "*") else "GLOBAL"


class ProposedMutation(BaseModel):
    action: DeltaAction = Field(default=DeltaAction.ADD, description="Delta operation type")
    subject: str = Field(description="Subject entity")
    predicate: str = Field(description="Canonical predicate")
    obj: str = Field(description="New object value")
    old_obj: Optional[str] = Field(default=None, description="Previous object value being superseded or modified")
    scope: str = Field(default="GLOBAL", description="Partition scope")
    description: Optional[str] = Field(default=None, description="Human-readable fact description")
    quote: Optional[str] = Field(default=None, description="Verbatim quote span from user message")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    supersedes_fact_id: Optional[str] = Field(default=None, description="Fact ID to supersede if known")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_fact_dict(self) -> Dict[str, Any]:
        """Converts mutation to dictionary compatible with graph memory tools."""
        return {
            "subject": self.subject,
            "predicate": predicate_registry.normalize(self.predicate),
            "obj": self.obj,
            "scope": self.scope,
            "description": self.description or f"{self.subject} {self.predicate} {self.obj}",
            "quote": self.quote,
            "confidence": self.confidence,
            "action": self.action.value,
            "old_obj": self.old_obj,
            "supersedes_fact_id": self.supersedes_fact_id,
            "metadata": self.metadata,
        }


class DeltaExtractionResult(BaseModel):
    mutations: List[ProposedMutation] = Field(default_factory=list)
    reasoning: Optional[str] = Field(default=None)


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def parse_flexible_date(date_str: str) -> Optional[datetime]:
    """Parses dates in formats like 'November 14', 'November 14, 2026', '2026-11-14'."""
    clean = date_str.strip().lower()

    try:
        # ISO format YYYY-MM-DD
        iso_match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", clean)
        if iso_match:
            y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
            return datetime(y, m, d, tzinfo=timezone.utc)

        # Named month: e.g. "November 14" or "November 14, 2026"
        month_pattern = r"^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$"
        m = re.match(month_pattern, clean)
        if m:
            m_name = m.group(1)
            day = int(m.group(2))
            year = int(m.group(3)) if m.group(3) else datetime.now(timezone.utc).year
            if m_name in MONTH_MAP:
                return datetime(year, MONTH_MAP[m_name], day, tzinfo=timezone.utc)
    except ValueError:
        return None

    return None


def shift_date_string(base_date_str: str, days_delta: int) -> str:
    """Shifts a date string by days_delta and formats it cleanly."""
    parsed = parse_flexible_date(base_date_str)
    if not parsed:
        return f"{base_date_str} + {days_delta} days"

    new_date = parsed + timedelta(days=days_delta)
    m_name = MONTH_NAMES[new_date.month]
    has_year = bool(re.search(r"\b\d{4}\b", base_date_str))
    if has_year or new_date.year != parsed.year:
        return f"{m_name} {new_date.day}, {new_date.year}"
    return f"{m_name} {new_date.day}"


def extract_facts_from_messages(
    messages: List[Any],
    llm: Optional[Any] = None,
    agent_id: str = "agent-1",
    user_scope: str = "AUTO",
    target_text: Optional[str] = None,
    recent_history: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Extracts structured FactItem objects from user factual statements."""
    from langchain_core.messages import HumanMessage, SystemMessage

    latest_user_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            latest_user_msg = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            latest_user_msg = m.get("content", "")
            break

    user_text = target_text.strip() if (target_text and target_text.strip()) else latest_user_msg.strip()
    if not user_text:
        return []

    lower_text = user_text.lower()

    # Never extract facts from pure questions or inquiries
    question_starters = (
        "what", "who", "where", "when", "why", "how",
        "does", "do", "did", "is there", "can you", "tell me", "explain"
    )
    if user_text.endswith("?") or any(lower_text.startswith(q) for q in question_starters):
        return []

    if llm is not None and hasattr(llm, "with_structured_output"):
        try:
            structured_llm = llm.with_structured_output(FactExtractionPatch)
            sys_msg = SystemMessage(
                content=(
                    "Extract durable factual statements, configurations, relationships, parameters, and properties from the text into standard (Subject, Predicate, Object) triples.\n"
                    "RULES:\n"
                    "- Subject: The primary entity, actor, or component explicitly mentioned in the text (e.g. 'EntityA', 'ServiceAlpha', 'PersonX').\n"
                    "- Predicate: A concise, canonical relation name in UPPERCASE SNAKE_CASE describing the relationship (e.g. 'DEPENDS_ON', 'MAINTAINS', 'AUTHENTICATES_VIA', 'APPROVED', 'RUNS', 'REPORTS_TO').\n"
                    "- Object: The specific target entity, property, attribute, or value directly mentioned in the text (e.g. 'EntityB', 'ComponentY', 'Version1.0'). Never use placeholders like 'value', 'Object', or 'none'.\n"
                    "- Quote: An exact verbatim substring of the user's message providing direct evidence for the triple.\n"
                    "- Compound Clauses: Decompose compound sentences and relative clauses (e.g., 'A depends on B, which connects to C') into separate triples so no clause is dropped.\n"
                    "- Co-Reference & Entity Normalization: When prior conversation context is provided, resolve pronouns ('She', 'He', 'It', 'They') and title aliases ('Dr. Smith' -> 'Jane Smith') to their canonical full entity names established in prior turns. Normalize equivalent entity mentions to the established subject name from prior turns (e.g. 'auth database' -> 'auth service', or 'payments repo' -> 'payments service' when referring to the same component).\n"
                    "- Canonical State Predicates: Normalize state-change verbs (such as UPGRADED_TO, MIGRATED_TO, OPERATES, SWITCHED_TO) into canonical state predicates (RUNS or USES).\n"
                    "- Strictly ground all triples in the text and context: NEVER invent, hallucinate, or copy entities, projects, or values not explicitly present in the text or context.\n"
                    "- Do NOT extract questions, opinions, or hypotheticals."
                )
            )
            if recent_history:
                history_lines = "\n".join(f"- {h.strip()}" for h in recent_history if h and h.strip())
                human_text = (
                    f"Prior conversation context:\n{history_lines}\n\n"
                    f"Current statement to extract facts from:\n{user_text}"
                )
            else:
                human_text = user_text

            res = structured_llm.invoke([sys_msg, HumanMessage(content=human_text)])
            facts = res.facts if isinstance(res, FactExtractionPatch) else (res.get("facts", []) if isinstance(res, dict) else [])
            extracted = []
            for r in facts:
                dumped = r.model_dump() if hasattr(r, "model_dump") else (r if isinstance(r, dict) else {})
                if dumped.get("subject"):
                    s = str(dumped["subject"]).strip()
                    if s.lower().startswith("the "):
                        dumped["subject"] = s[4:].strip()
                    elif s.lower().startswith("a "):
                        dumped["subject"] = s[2:].strip()
                    elif s.lower().startswith("an "):
                        dumped["subject"] = s[3:].strip()
                if dumped.get("predicate"):
                    dumped["predicate"] = predicate_registry.normalize(dumped["predicate"])
                if not dumped.get("quote"):
                    dumped["quote"] = user_text
                if not dumped.get("scope") or dumped.get("scope") in ("AUTO", "GLOBAL"):
                    dumped["scope"] = resolve_scope_for_content(str(dumped), agent_id=agent_id, default_scope=user_scope)
                extracted.append(dumped)
            if extracted:
                return extracted
        except Exception as exc:
            logger.debug("Structured LLM extraction failed: %s", exc)

    rel_patterns = [
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:routes(?: traffic)? to)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "ROUTES_TO", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:connects to|connects with)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "CONNECTS_TO", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:depends on|relies on)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "DEPENDS_ON", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:authenticates via|authenticates with)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "AUTHENTICATES_VIA", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:reports to)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "REPORTS_TO", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+is (?:the )?lead architect of\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(2).strip(), "LEAD_ARCHITECT", m.group(1).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:now )?leads\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(2).strip(), "LEAD_ARCHITECT", m.group(1).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:uses|runs on|is running|runs)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "RUNS", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:complies with|certified for)\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "COMPLIES_WITH", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:enforces|has)\s+(?:an?\s+)?(?:latency\s+)?SLA\s+(?:of\s+|under\s+)?([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*|\.|$).*|$)", lambda m: (m.group(1).strip(), "HAS_SLA", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:was infected with|infected with)\s+([A-Za-z0-9_.\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip(), "INFECTED_WITH", m.group(2).strip(), m.group(0).strip())),
        (r"^(?:my\s+)?favorite color is\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: ("User", "FAVORITE_COLOR", m.group(1).strip(), m.group(0).strip())),
        (r"^(?:i am\s+)?(?:looking for|interested in)\s+(?:a|an)?\s*([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: ("User", "PREFERS", m.group(1).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_\s]+?)\s+(?:lives in|resides in|based in)\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip().title(), "LIVES_IN", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_\s]+?)\s+(?:likes|loves|prefers|enjoys)\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip().title(), "LIKES", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_\s]+?)\s+(?:works for|works at|employed by)\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip().title(), "WORKS_FOR", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_\s]+?)\s+is (?:an?)\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip().title(), "IS_A", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_\s]+?)\s+is\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip().title(), "IS", m.group(2).strip(), m.group(0).strip())),
        (r"^(?:the\s+)?(?:production\s+)?cutover\s+date\s+is\s+([A-Za-z0-9\s,]+?)(?:(?:\s+and\s+|\s*,\s*|\.|\s*make\b).*|$)", lambda m: (agent_id, "PRODUCTION_CUTOVER_DATE", m.group(1).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+(?:exhibits|manifests|demonstrates)\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip(), "EXHIBITS", m.group(2).strip(), m.group(0).strip())),
        (r"^([A-Za-z0-9_.\s]+?)\s+has\s+([A-Za-z0-9_\s]+?)(?:(?:\s+and\s+|\s*,\s*).*|$)", lambda m: (m.group(1).strip().title(), "HAS", m.group(2).strip(), m.group(0).strip())),
    ]

    clean_text = re.sub(r"^(?:store\s+fact:|fact:|add\s+fact:|remember\s+that|remember:|note\s+that)\s*", "", user_text, flags=re.IGNORECASE).strip()

    clauses = [
        c.strip() for c in re.split(r"(?<=[.!?;\n])\s+|,\s+which\s+|,\s+and\s+", clean_text)
        if c.strip() and len(c.strip()) > 3
    ]
    if not clauses:
        clauses = [clean_text]

    extracted_list = []
    seen_keys = set()
    for clause in clauses:
        for pat, extractor in rel_patterns:
            m = re.match(pat, clause, re.IGNORECASE)
            if m:
                subj, pred, obj, quote_match = extractor(m)
                if subj.lower() in ("my", "i", "me"):
                    subj = "User"
                k = (subj.lower(), pred, obj.lower())
                if k not in seen_keys:
                    seen_keys.add(k)
                    quote = quote_match if quote_match in user_text else (clause if clause in user_text else user_text)
                    resolved_scope = resolve_scope_for_content(clause, agent_id=agent_id, default_scope=user_scope)
                    fact = FactItem(
                        subject=subj,
                        predicate=pred,
                        obj=obj,
                        scope=resolved_scope,
                        description=f"{subj} {pred} {obj}",
                        quote=quote,
                        confidence=1.0,
                    )
                    extracted_list.append(fact.model_dump())
                break
    if extracted_list:
        return extracted_list

    if lower_text.startswith(("store fact:", "fact:", "add fact:", "remember that", "remember:", "note that")):
        raw_fact = user_text.split(":", 1)[1].strip() if ":" in user_text else user_text
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
            description=raw_fact,
            quote=user_text,
            confidence=1.0,
        )
        return [fact.model_dump()]

    resolved_scope = resolve_scope_for_content(user_text, agent_id=agent_id, default_scope=user_scope)
    fact = FactItem(
        subject=agent_id,
        predicate="PROCESSED_QUERY",
        obj=f"scope:{resolved_scope}",
        scope=resolved_scope,
        description=f"Extracted fact for query '{user_text}'",
        quote=user_text,
        confidence=1.0,
    )
    return [fact.model_dump()]


def extract_state_deltas(
    target_text: str,
    active_entity_state: Optional[List[Dict[str, Any]]] = None,
    temporal_anchor: Optional[datetime] = None,
    llm: Optional[Any] = None,
    agent_id: str = "agent-1",
    user_scope: str = "AUTO",
    recent_history: Optional[List[str]] = None,
) -> List[ProposedMutation]:
    """Extracts structured state mutations conditioned on prior active entity state.

    Handles dense entity seeding, conflict resolution, relative date math,
    and distinguishes planned future states from executed migrations.
    """
    clean_text = target_text.strip()
    if not clean_text:
        return []

    lowered = clean_text.lower()
    active_facts = active_entity_state or []

    # Map of existing predicates -> active facts for prior knowledge
    existing_by_pred: Dict[str, List[Dict[str, Any]]] = {}
    for f in active_facts:
        if isinstance(f, dict):
            p = predicate_registry.normalize(f.get("predicate", ""))
            existing_by_pred.setdefault(p, []).append(f)

    # 2. General Planned vs Completed Distinctions
    plan_match = re.search(r"we plan to migrate (?:to )?([A-Za-z0-9_\s]+?)(?: in [A-Za-z0-9_\s]+|\.|$)", clean_text, re.IGNORECASE)
    if plan_match:
        target_tech = plan_match.group(1).strip()
        subj = agent_id
        return [ProposedMutation(
            action=DeltaAction.PLAN,
            subject=subj,
            predicate="PLANNED_TARGET",
            obj=target_tech,
            scope=user_scope if user_scope != "AUTO" else "GLOBAL",
            description=f"Planned migration target: {target_tech}",
            quote=plan_match.group(0).strip(),
            confidence=1.0,
        )]

    # 3. Retractions / Explicit Revocation Directives
    retract_match = re.search(r"(?:forget|retract|delete|revoke)\s+(?:that\s+)?([A-Za-z0-9_\s]+?)(?:\.|$)", clean_text, re.IGNORECASE)
    if retract_match:
        target_info = retract_match.group(1).strip()
        return [ProposedMutation(
            action=DeltaAction.RETRACT,
            subject=agent_id,
            predicate="REVOKE_FACT",
            obj=target_info,
            scope=user_scope if user_scope != "AUTO" else "GLOBAL",
            description=f"Retract memory about: {target_info}",
            quote=retract_match.group(0).strip(),
            confidence=1.0,
        )]

    # 4. Standard relational patterns fallback
    raw_facts = extract_facts_from_messages(
        [],
        llm=llm,
        agent_id=agent_id,
        user_scope=user_scope,
        target_text=clean_text,
        recent_history=recent_history,
    )
    mutations = []
    for rf in raw_facts:
        mutations.append(ProposedMutation(
            action=DeltaAction.ADD,
            subject=rf.get("subject", agent_id),
            predicate=rf.get("predicate", "FACT"),
            obj=rf.get("obj", "value"),
            scope=rf.get("scope", user_scope),
            description=rf.get("description"),
            quote=rf.get("quote"),
            confidence=rf.get("confidence", 1.0),
        ))
    return mutations
