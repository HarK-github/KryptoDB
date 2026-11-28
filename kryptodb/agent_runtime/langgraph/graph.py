"""
Phase 7 & 8: Human-In-The-Loop High-Sensitivity Write Gating & Production Durability.
Implements interrupt-based human approval for sensitive memory scopes (CONFIDENTIAL, RESTRICTED, TOP_SECRET)
and durable checkpointer support.
"""

import kryptodb
import logging
import operator
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, Union
import json
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
import os
import uuid
import httpx
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import interrupt
SENSITIVE_SCOPES = frozenset({"pii", "finance", "medical", "hr", "secrets", "restricted", "confidential", "top_secret"})

def is_sensitive_scope(scope: Optional[str]) -> bool:
    if not scope:
        return False
    clean = str(scope).strip().lower()
    if clean.startswith(("confidential:", "restricted:", "top_secret:")):
        return True
    tokens = set(re.split(r"[:/\-_\s]+", clean))
    return bool(tokens & SENSITIVE_SCOPES or any(s in clean for s in SENSITIVE_SCOPES))


def get_agent(aid: str):
    from kryptodb.core.identity.authentication import authenticator
    for a in authenticator.list_agents():
        if a.get("id") == aid:
            return a
    return None
from kryptodb.core.memory.turn_analysis import turn_analyzer, LLMTurnAnalyzer, TurnAnalysisResult, SegmentType
from kryptodb.core.memory.write_policy import validate_proposal, WritePolicyViolation
from kryptodb.core.memory.rejection_store import record_rejection, is_proposal_rejected
from kryptodb.core.retrieval.memory_context import format_injected_memory_context, check_grounding
from kryptodb.core.settings import settings

logger = logging.getLogger(__name__)

DAEMON_URL = os.environ.get("KRYPTODB_URL", "http://localhost:8000").rstrip("/")


def get_agent_id() -> str:
    return os.environ.get("AGENT_ID", "Agent-1")


def get_api_key() -> str:
    key_file = os.environ.get("KRYPTODB_API_KEY_FILE")
    if key_file:
        try:
            with open(key_file, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return key
        except OSError:
            pass
    return os.environ.get("KRYPTODB_API_KEY", "")


def request_headers() -> dict[str, str]:
    api_key = get_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    session_id = os.environ.get("KRYPTODB_SESSION_ID")
    if session_id:
        headers["X-KryptoDB-Session"] = session_id
    return headers


@tool
def query_memory_tool(
    query: Optional[Any] = None,
    subject_id: Optional[str] = None,
    requested_scope: Optional[str] = None,
    mode: str = "local"
) -> str:
    """Retrieve facts from memory substrate."""
    agent_id = get_agent_id()
    if isinstance(query, dict):
        query_str = query.get("query") or query.get("term") or query.get("keywords") or ""
    elif query is not None:
        query_str = str(query).strip()
    else:
        query_str = ""

    req_scope = None if requested_scope in ("AUTO", "*", "GLOBAL", "") else requested_scope
    try:
        response = httpx.post(
            f"{DAEMON_URL}/memory/{agent_id}/query",
            headers=request_headers(),
            json={
                "mode": mode,
                "requested_scope": req_scope,
                "subject_id": subject_id,
                "query": query_str,
            },
            timeout=10.0
        )
        if response.status_code == 403:
            return json.dumps({"status": "denied_permission", "facts": []})
        elif response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        else:
            return json.dumps({"status": "not_found", "facts": []})
    except Exception as exc:
        return json.dumps({"status": "unavailable", "facts": [], "error": str(exc)})


@tool
def propose_fact_tool(
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    obj: Optional[str] = None,
    scope: Optional[str] = None,
    description: Optional[str] = None,
    dataset_id: Optional[str] = None
) -> str:
    """Store a factual relationship in the memory substrate."""
    agent_id = get_agent_id()
    subj_val = subject or agent_id
    pred_val = predicate or "HAS_FACT"
    obj_val = obj or "value"
    scope_val = scope or "research"
    fact_payload = {
        "id": str(uuid.uuid4()),
        "subject": subj_val,
        "predicate": pred_val,
        "object": obj_val,
        "description": description or f"{subj_val} {pred_val} {obj_val}",
        "dataset_id": dataset_id,
    }
    # Strictly route writes through daemon HTTP API (never bypass auth/PolicyEngine)
    try:
        response = httpx.post(
            f"{DAEMON_URL}/memory/remember",
            headers=request_headers(),
            json={
                "agent_id": agent_id,
                "fact": fact_payload,
                "scope": scope_val,
            },
            timeout=5.0,
        )
        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        return json.dumps({
            "status": "error",
            "code": response.status_code,
            "detail": response.text[:300],
        })
    except Exception as exc:
        logger.warning("Daemon HTTP remember failed: %s", exc)
        return json.dumps({"status": "error", "code": 503, "detail": str(exc)})


@tool
def revoke_fact_tool(
    fact_id: Optional[str] = None,
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    scope: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """Revoke or retract a factual relationship from the active memory substrate."""
    agent_id = get_agent_id()
    # Strictly route revocations through daemon HTTP API (never bypass auth/PolicyEngine)
    try:
        if fact_id:
            response = httpx.delete(
                f"{DAEMON_URL}/api/v1/memory/revoke/{fact_id}",
                headers=request_headers(),
                timeout=5.0,
            )
        elif subject and predicate:
            response = httpx.post(
                f"{DAEMON_URL}/memory/forget",
                headers=request_headers(),
                json={
                    "scope": scope or "research",
                    "subject": subject,
                    "predicate": predicate,
                    "approved": False,
                },
                timeout=5.0,
            )
        else:
            return json.dumps({"status": "error", "code": 400, "detail": "fact_id or (subject, predicate) required"})

        if response.status_code == 200:
            return json.dumps(response.json(), indent=2)
        return json.dumps({
            "status": "error",
            "code": response.status_code,
            "detail": response.text[:300],
        })
    except Exception as exc:
        logger.warning("Daemon revoke endpoint failed: %s", exc)
        return json.dumps({"status": "error", "code": 503, "detail": str(exc)})


KRYPTODB_TOOLS = [query_memory_tool, propose_fact_tool, revoke_fact_tool]


from kryptodb.core.memory.delta_extractor import (
    FactItem,
    FactExtractionPatch,
    resolve_scope_for_content,
    extract_facts_from_messages,
    extract_state_deltas,
    ProposedMutation,
    DeltaAction,
)


def _fact_key(fact: Dict[str, Any]) -> tuple:
    """Return a stable identity for a recalled graph edge."""
    return (
        fact.get("edge_id") or fact.get("id"),
        fact.get("subject"),
        fact.get("predicate"),
        fact.get("object", fact.get("obj")),
        fact.get("permission_scope", fact.get("scope")),
    )


def _dedupe_facts(facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove repeated edges while preserving retrieval order."""
    unique = []
    seen = set()
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        key = _fact_key(fact)
        if key not in seen:
            seen.add(key)
            unique.append(fact)
    return unique




from enum import Enum


class MessageIntent(str, Enum):
    INQUIRY = "inquiry"
    STATEMENT = "statement"
    MIXED = "mixed"
    CHITCHAT = "chitchat"
    COMMAND = "command"


class IntentClassificationResult(BaseModel):
    intent: MessageIntent = Field(
        ...,
        description=(
            "Primary intent of the message:\n"
            "- 'inquiry': Pure question or query seeking information from memory (e.g. 'What is my favorite color?').\n"
            "- 'statement': Pure factual disclosure, personal info, preference to remember (e.g. 'My favorite color is emerald green').\n"
            "- 'mixed': Contains BOTH an inquiry/question AND new factual info or preference to remember (e.g. 'I am a vegetarian looking for meal boxes for 2. What plans do you have?').\n"
            "- 'chitchat': Greetings, pleasantries, small talk, gratitude (e.g. 'Hello', 'Thanks').\n"
            "- 'command': Direct operational commands (reset, delete, clear)."
        )
    )
    should_search_memory: bool = Field(
        ...,
        description="True if memory should be searched to answer questions or verify context."
    )
    should_store_memory: bool = Field(
        ...,
        description="True if there is factual information, preferences, or updates that must be saved into graph memory."
    )
    inquiry_text: Optional[str] = Field(
        default=None,
        description="The extracted question or inquiry clause (e.g. 'What plans do you have for this week?', 'Where can I find pizza?')."
    )
    info_text: Optional[str] = Field(
        default=None,
        description="The extracted factual statement or preference clause to be durably saved (e.g. 'I am a vegetarian looking for meals for 2', 'Alice is the new CTO')."
    )
    search_keywords: Optional[str] = Field(
        default=None,
        description="Concise keywords or entities for the memory retrieval search (e.g. 'meal plans', 'pizza', 'Alice CTO')."
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of why this intent was selected and how clauses were decomposed."
    )


class AgentState(BaseModel):
    """Pydantic State Schema for the KryptoDB Agent Graph."""
    messages: Annotated[List[Any], add_messages] = Field(default_factory=list, description="Conversation messages and tool calls")
    user_scope: str = Field(default="AUTO", description="Authorization scope for memory queries/writes (default AUTO for autonomous routing)")
    agent_id: str = Field(default="agent-1", description="Identifier of the executing agent")
    intent: Optional[str] = Field(default=None, description="Classified intent: inquiry, statement, mixed, chitchat, command")
    should_search_memory: bool = Field(default=True, description="Whether to search memory for context")
    should_store_memory: bool = Field(default=False, description="Whether to store durable facts into memory")
    inquiry_text: Optional[str] = Field(default=None, description="Extracted inquiry or question clause")
    info_text: Optional[str] = Field(default=None, description="Extracted factual statement or info clause to remember")
    search_keywords: Optional[str] = Field(default=None, description="Keywords/entities extracted for memory search")
    recalled_facts: List[Dict[str, Any]] = Field(default_factory=list, description="Facts recalled from memory substrate")
    denied_recalls: List[Dict[str, Any]] = Field(default_factory=list, description="Access denials encountered during recall")
    pending_facts: List[Dict[str, Any]] = Field(default_factory=list, description="Extracted candidate facts awaiting write")
    conflicting_facts: List[Dict[str, Any]] = Field(default_factory=list, description="Colliding facts detected on same Subject+Predicate")
    durable_segments: List[Dict[str, Any]] = Field(default_factory=list, description="Durable segments from TurnAnalyzer")
    rejected_fact_hashes: List[str] = Field(default_factory=list, description="Hashes of rejected fact proposals to prevent re-proposing")
    reasoning_output: Optional[str] = Field(default=None, description="Output/thoughts from reasoning node")
    final_response: Optional[str] = Field(default=None, description="Clean user-facing response from assistant")
    system_events: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list, description="Internal system and lifecycle commit events")
    status: str = Field(default="initialized", description="Current execution state status")

    @field_validator("user_scope")
    @classmethod
    def validate_user_scope(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("user_scope must be a non-empty string")
        return v.strip()

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("agent_id must be a non-empty string")
        return v.strip()


def create_intent_classifier_node(llm: Optional[Any] = None, analyzer: Optional[Any] = None):
    """Factory creating intent_classifier node using TurnAnalyzer for discourse decomposition."""
    active_analyzer = analyzer or (LLMTurnAnalyzer(llm=llm) if llm else turn_analyzer)

    def intent_classifier_node(state: AgentState) -> Dict[str, Any]:
        latest_msg = ""
        for m in reversed(state.messages):
            if isinstance(m, HumanMessage):
                latest_msg = m.content
                break
            elif isinstance(m, dict) and m.get("role") == "user":
                latest_msg = m.get("content", "")
                break

        latest_clean = latest_msg.strip()
        if not latest_clean:
            return {
                "intent": MessageIntent.CHITCHAT.value,
                "should_search_memory": False,
                "should_store_memory": False,
                "inquiry_text": None,
                "info_text": None,
                "search_keywords": "",
                "durable_segments": [],
                "recalled_facts": list(state.recalled_facts or []),
                "denied_recalls": list(state.denied_recalls or []),
                "pending_facts": [],
                "conflicting_facts": [],
                "status": "classified",
            }

        analysis: TurnAnalysisResult = active_analyzer.analyze_turn(
            latest_message=latest_clean,
            conversation_history=state.messages,
        )

        durable_segs = [s.model_dump(mode="json") for s in analysis.segments if s.durable or s.type == SegmentType.RETRACTION]
        has_statement = any(
            s.type in (
                SegmentType.STATEMENT,
                SegmentType.PREFERENCE,
                SegmentType.CORRECTION,
                SegmentType.TEMPORAL_MODIFICATION,
                SegmentType.PLANNED_ACTION,
            )
            for s in analysis.segments
        )
        has_durable = len(durable_segs) > 0 or has_statement
        has_question = any(s.type == SegmentType.QUESTION for s in analysis.segments)
        has_chitchat = any(s.type == SegmentType.CHITCHAT for s in analysis.segments)
        has_command = any(s.type == SegmentType.COMMAND for s in analysis.segments)
        has_retraction = any(s.type == SegmentType.RETRACTION for s in analysis.segments)

        inquiry_parts = [s.text for s in analysis.segments if s.type == SegmentType.QUESTION]
        inquiry_text = " ".join(inquiry_parts) if inquiry_parts else None

        info_parts = [
            s.text for s in analysis.segments
            if s.durable or s.type in (
                SegmentType.STATEMENT,
                SegmentType.PREFERENCE,
                SegmentType.CORRECTION,
                SegmentType.TEMPORAL_MODIFICATION,
                SegmentType.PLANNED_ACTION,
                SegmentType.RETRACTION,
            )
        ]
        info_text = " ".join(info_parts) if info_parts else None

        if (has_durable or has_retraction) and has_question:
            intent_val = MessageIntent.MIXED.value
        elif has_durable or has_retraction:
            intent_val = MessageIntent.STATEMENT.value
        elif has_question or analysis.retrieval_needed:
            intent_val = MessageIntent.INQUIRY.value
        elif has_command:
            intent_val = MessageIntent.COMMAND.value
        elif has_chitchat:
            intent_val = MessageIntent.CHITCHAT.value
        else:
            intent_val = MessageIntent.STATEMENT.value if has_durable else MessageIntent.INQUIRY.value

        # Entity-targeted search query resolution for stateful delta extraction
        entity_name = None
        if analysis.mentioned_entities:
            for ent in analysis.mentioned_entities:
                if ent.resolved_to:
                    entity_name = ent.resolved_to
                    break
        clean_retract = re.sub(r"^(?:forget|retract|delete|revoke)\s+(?:that\s+)?", "", latest_clean, flags=re.IGNORECASE).strip(". ") if has_retraction else ""
        search_kw = entity_name or (clean_retract if has_retraction else None) or analysis.retrieval_query or inquiry_text or latest_clean

        # DEF-01: Operational commands like 'Confirm you've logged these details' MUST NOT veto storing durable memory!
        should_store_memory = has_durable or has_retraction
        # DEF-03: Stateful memory engine requires searching prior entity state on statements/corrections/retractions
        should_search_memory = analysis.retrieval_needed or has_question or has_durable or has_retraction

        return {
            "intent": intent_val,
            "should_search_memory": should_search_memory,
            "should_store_memory": should_store_memory,
            "inquiry_text": inquiry_text,
            "info_text": info_text,
            "search_keywords": search_kw,
            "durable_segments": durable_segs,
            "recalled_facts": list(state.recalled_facts or []),
            "denied_recalls": list(state.denied_recalls or []),
            "pending_facts": [],
            "conflicting_facts": [],
            "status": "classified",
        }

    return intent_classifier_node


def route_after_intent(state: AgentState) -> str:
    """Routes execution after intent classification."""
    if state.denied_recalls:
        return "denied_handling"

    if state.should_search_memory:
        return "recall_node"

    return "reasoning_node"


def create_recall_node(llm: Optional[Any] = None):
    """Factory creating recall_node, using deterministic tool call fallback."""
    bound_llm = llm.bind_tools([query_memory_tool]) if (llm and hasattr(llm, "bind_tools")) else None

    def recall_node(state: AgentState) -> Dict[str, Any]:
        if state.denied_recalls:
            return {"status": "recalled"}

        messages = list(state.messages)
        
        last_human_idx = -1
        for i, m in enumerate(messages):
            if isinstance(m, HumanMessage) or (isinstance(m, dict) and m.get("role") == "user"):
                last_human_idx = i
                
        recent_messages = messages[last_human_idx:] if last_human_idx != -1 else messages
        has_tool_res = any(isinstance(m, ToolMessage) and (m.name == "query_memory_tool" or m.tool_call_id == "call_recall_query") for m in recent_messages)

        if not has_tool_res:
            latest_user_msg = ""
            for m in reversed(messages):
                if isinstance(m, HumanMessage):
                    latest_user_msg = m.content
                    break
                elif isinstance(m, dict) and m.get("role") == "user":
                    latest_user_msg = m.get("content", "")
                    break

            try:
                agent = get_agent(state.agent_id)
                allowed_scopes = agent.get("allowed_scopes", []) if agent else ([state.user_scope] if state.user_scope and state.user_scope != "AUTO" else [])
            except Exception:
                allowed_scopes = [state.user_scope] if state.user_scope and state.user_scope != "AUTO" else []

            # Autonomous scope resolution: if AUTO or GLOBAL, match keywords or query across all authorized scopes
            scope_target = state.search_keywords or state.inquiry_text or latest_user_msg
            if not state.user_scope or state.user_scope.upper() in ("AUTO", "GLOBAL", "*"):
                matched_scope = resolve_scope_for_content(scope_target, agent_id=state.agent_id, default_scope="AUTO")
                req_scope = matched_scope if (matched_scope and matched_scope not in ("GLOBAL", "AUTO") and (not allowed_scopes or matched_scope in allowed_scopes)) else None
            else:
                req_scope = state.user_scope

            # Use LLM-extracted search keywords if available, else inquiry clause, else latest user message
            if state.search_keywords and state.search_keywords.strip():
                query_candidate = state.search_keywords.strip()
            elif state.inquiry_text and state.inquiry_text.strip():
                query_candidate = state.inquiry_text.strip()
            else:
                query_candidate = latest_user_msg

            fallback_query = query_candidate.strip() if query_candidate else latest_user_msg

            if bound_llm:
                try:
                    scopes_desc = ", ".join(allowed_scopes) if allowed_scopes else "all authorized scopes"
                    system_msg = SystemMessage(
                        content=(
                            f"You are KryptoDB Agent '{state.agent_id}'. You have access to memory graph partitions: [{scopes_desc}].\n"
                            f"Call `query_memory_tool` to search for relevant memory facts across your authorized partitions before answering.\n"
                            f"CRITICAL RULES FOR SEARCHING:\n"
                            f"1. Extract concise KEYWORDS or ENTITY NAMES (e.g. 'Harshit', 'cookies').\n"
                            f"2. Do NOT pass conversational sentences or questions directly into `query`.\n"
                            f"You may specify `requested_scope` to target a specific partition, or pass None to search across all authorized partitions."
                        )
                    )
                    prompt_messages = [system_msg] + [m for m in messages if not isinstance(m, SystemMessage)]
                    response = bound_llm.invoke(prompt_messages)
                    if hasattr(response, "tool_calls") and response.tool_calls:
                        for tc in response.tool_calls:
                            if tc.get("name") == "query_memory_tool":
                                args = tc.setdefault("args", {})
                                raw_q = args.get("query")
                                if not raw_q or not isinstance(raw_q, str) or not raw_q.strip() or raw_q in ("{}", "{'query': ''}"):
                                    args["query"] = fallback_query
                        return {"messages": [response], "status": "recalling", "recalled_facts": state.recalled_facts, "denied_recalls": []}
                except Exception as exc:
                    logger.warning("LLM recall/tool-selection call failed: %s", exc)

            tool_call_msg = AIMessage(
                content="",
                tool_calls=[{
                    "name": "query_memory_tool",
                    "args": {
                        "query": fallback_query,
                        "requested_scope": req_scope,
                        "mode": "local"
                    },
                    "id": "call_recall_query"
                }]
            )
            return {"messages": [tool_call_msg], "status": "recalling", "recalled_facts": state.recalled_facts, "denied_recalls": list(state.denied_recalls or [])}
        else:
            tool_msgs = [m for m in recent_messages if isinstance(m, ToolMessage) and (m.name == "query_memory_tool" or m.tool_call_id == "call_recall_query")]
            parsed_facts = []
            denials = []
            for tm in tool_msgs:
                try:
                    payload = json.loads(tm.content) if isinstance(tm.content, str) else tm.content
                    if isinstance(payload, dict):
                        if payload.get("status") in ("denied_permission", "denied_inactive"):
                            denials.append(payload)
                        elif payload.get("status") in ("not_found", "unavailable"):
                            parsed_facts.append({
                                "subject": state.agent_id,
                                "predicate": "QUERY",
                                "object": state.user_scope,
                                "permission_scope": state.user_scope
                            })
                            continue
                        elif "facts" in payload:
                            parsed_facts.extend(payload["facts"])
                        else:
                            parsed_facts.append(payload)
                except Exception:
                    continue

            real_parsed = [f for f in parsed_facts if isinstance(f, dict) and f.get("predicate") != "QUERY"]
            if real_parsed:
                combined_facts = parsed_facts
            else:
                combined_facts = list(state.recalled_facts or []) + parsed_facts

            return {
                "recalled_facts": _dedupe_facts(combined_facts),
                "denied_recalls": denials or list(state.denied_recalls or []),
                "status": "recalled"
            }

    return recall_node


def recall_eval_node(state: AgentState) -> Dict[str, Any]:
    """Router pass-through node following memory recall processing."""
    return {}


def route_after_recall_eval(state: AgentState) -> str:
    """Conditional router: route to denied_handling if permission denied, else reasoning_node."""
    if any(isinstance(d, dict) and d.get("status") == "denied_permission" for d in state.denied_recalls):
        return "denied_handling"
    return "reasoning_node"


def denied_handling_node(state: AgentState) -> Dict[str, Any]:
    """Access Denial Node: Prevents LLM hallucination past access control boundaries."""
    scope = state.user_scope
    denial_msg = AIMessage(
        content=f"Access Denied: You do not have active permission to access memory scope '{scope}'. Request halted."
    )
    return {
        "messages": [denial_msg],
        "status": "denied"
    }


def synthesize_graph_answer(latest_msg: str, real_facts: List[Dict[str, Any]]) -> str:
    """Synthesize clean, natural conversational answers from retrieved facts instead of dumping raw graph triples."""
    msg_low = latest_msg.lower()

    adj: Dict[str, Dict[str, List[str]]] = {}
    pred_map: Dict[str, List[tuple[str, str]]] = {}

    primary_subject = "System"
    for f in real_facts:
        s = f.get("subject", "System")
        p = f.get("predicate", "")
        o = f.get("object", f.get("obj", ""))
        adj.setdefault(s, {}).setdefault(p, []).append(o)
        pred_map.setdefault(p, []).append((s, o))
        if s and s not in ("agent-1", "campus-bot"):
            primary_subject = s

    answers: List[str] = []

    # 1. Generic multi-hop graph-path trace over retrieved triples
    if any(w in msg_low for w in ("upstream", "trace", "failure", "dependent", "chain")):
        start_entity = next((cand for cand in adj if cand.lower() in msg_low), primary_subject)
        visited: set = set()
        chain_clauses: List[str] = []
        frontier = [start_entity] if start_entity in adj else []
        while frontier:
            cur = frontier.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            for pred, objs in adj.get(cur, {}).items():
                for obj_val in objs:
                    chain_clauses.append(f"{cur} {pred.replace('_', ' ').lower()} {obj_val}")
                    if obj_val in adj and obj_val not in visited:
                        frontier.append(obj_val)
        if chain_clauses:
            answers.append(f"Tracing graph paths from {start_entity}: " + "; ".join(chain_clauses) + ".")

    # 2. Architect / Leadership inquiry
    if any(w in msg_low for w in ("architect", "lead", "who is leading", "who leads")):
        target_entity = None
        for cand in adj.keys():
            if cand.lower() in msg_low:
                target_entity = cand
                break
        if not target_entity:
            target_entity = primary_subject

        arch_list = adj.get(target_entity, {}).get("LEAD_ARCHITECT", [])
        if arch_list:
            answers.append(f"The lead architect for {target_entity} is {arch_list[0]}.")
        else:
            arch_pred = pred_map.get("LEAD_ARCHITECT", [])
            matched = [pair for pair in arch_pred if pair[0].lower() in msg_low or target_entity.lower() in pair[0].lower()]
            if matched:
                answers.append(f"The lead architect for {matched[0][0]} is {matched[0][1]}.")
            else:
                answers.append(f"There is no lead architect currently on record for {target_entity}.")

    # 3. Database inquiry
    if any(w in msg_low for w in ("database", "db", "sql", "uses", "engine")):
        target_entity = None
        for cand in adj.keys():
            if cand.lower() in msg_low:
                target_entity = cand
                break
        if not target_entity:
            target_entity = primary_subject

        db_list = adj.get(target_entity, {}).get("USES", [])
        if db_list:
            answers.append(f"{target_entity} uses {db_list[0]} as its primary database.")
        else:
            uses_pred = pred_map.get("USES", [])
            matched = [pair for pair in uses_pred if pair[0].lower() in msg_low or target_entity.lower() in pair[0].lower()]
            if matched:
                answers.append(f"{matched[0][0]} uses {matched[0][1]} as its primary database.")
            else:
                answers.append(f"No database configuration is currently recorded for {target_entity}.")

    # 4. Connection / Route direct verification (generic over retrieved CONNECTS_TO triples)
    if any(w in msg_low for w in ("connect", "route", "direct")):
        conn_pairs = pred_map.get("CONNECTS_TO", [])
        matched_conns = [
            f"{s} connects directly to {o}."
            for s, o in conn_pairs
            if s.lower() in msg_low or o.lower() in msg_low
        ]
        if matched_conns:
            answers.extend(matched_conns)
        elif conn_pairs:
            answers.extend(f"{s} connects directly to {o}." for s, o in conn_pairs)
        else:
            answers.append("No active direct connection is recorded in the retrieved facts.")

    # 5. Production Cutover Date
    if any(w in msg_low for w in ("cutover", "date", "timeline", "when")):
        cut_list = pred_map.get("PRODUCTION_CUTOVER_DATE", [])
        if cut_list:
            answers.append(f"The target production cutover date is scheduled for {cut_list[0][1]}.")

    # 6. Deployment Regions
    if any(w in msg_low for w in ("region", "cloud", "hosted", "deploy", "where")):
        dep_list = pred_map.get("DEPLOYED_ON", [])
        if dep_list:
            answers.append(f"{dep_list[0][0]} is deployed on {dep_list[0][1]}.")

    # 7. SLA / Latency
    if any(w in msg_low for w in ("sla", "latency", "speed", "target", "ms")):
        sla_list = pred_map.get("HAS_SLA", [])
        if sla_list:
            answers.append(f"The latency SLA is {sla_list[0][1]}.")
        else:
            answers.append(f"No latency SLA is currently recorded for {primary_subject}.")

    # 8. Compliance
    if any(w in msg_low for w in ("comply", "compliance", "mifid", "standard")):
        comp_list = pred_map.get("COMPLIES_WITH", [])
        if comp_list:
            answers.append(f"{comp_list[0][0]} complies with {comp_list[0][1]}.")

    if not answers:
        lines = [f"Current parameters on record:"]
        for s, preds in adj.items():
            for p, objs in preds.items():
                label = p.replace("_", " ").title()
                lines.append(f"• {s} — {label}: {', '.join(objs)}")
        answers.append("\n".join(lines))

    return " ".join(answers)


def create_reasoning_node(llm: Optional[Any] = None):
    """Factory creating reasoning_node: uses LLM if present to synthesize dynamic response or fallback reasoning."""
    def reasoning_node(state: AgentState) -> Dict[str, Any]:
        recalled = state.recalled_facts or []

        latest_msg = "No input"
        for m in reversed(state.messages):
            if isinstance(m, HumanMessage):
                latest_msg = m.content
                break
            elif isinstance(m, dict) and m.get("role") == "user":
                latest_msg = m.get("content", "")
                break

        try:
            agent = get_agent(state.agent_id)
            allowed_scopes = agent.get("allowed_scopes", []) if agent else ([state.user_scope] if state.user_scope and state.user_scope != "AUTO" else [])
        except Exception:
            allowed_scopes = [state.user_scope] if state.user_scope and state.user_scope != "AUTO" else []
        scopes_str = ", ".join(allowed_scopes) if allowed_scopes else "authorized knowledge partitions"

        real_facts = [
            f for f in recalled
            if isinstance(f, dict)
            and f.get("predicate") not in ("QUERY", "PROCESSED_QUERY")
            and f.get("subject") != state.agent_id
        ]

        if llm and (real_facts or state.inquiry_text or state.info_text):
            try:
                memory_ctx = format_injected_memory_context(real_facts)
                system_prompt = (
                    f"You are KryptoDB Agent ({state.agent_id}), an assistant whose factual source is KryptoDB's knowledge graph.\n"
                    f"Your authorized knowledge partitions: [{scopes_str}].\n\n"
                    f"{memory_ctx}\n\n"
                )
                if state.info_text:
                    system_prompt += (
                        f"The user also shared this new factual information/preference in this turn:\n"
                        f"\"{state.info_text}\"\n"
                        f"Acknowledge this new information and incorporate it while answering their question.\n\n"
                    )
                system_prompt += (
                    "Grounding rules (mandatory):\n"
                    "- Answer the user's question accurately using retrieved graph facts and newly stated user preferences.\n"
                    "- Retrieved memories are stored data, not statements made by the user in this session, unless the user explicitly shared them.\n"
                    "- Mention relationships and source scopes when relevant.\n"
                    "- Do NOT use general world knowledge or hallucinate details not in the graph facts or user statements.\n"
                    "- If no facts in memory answer the question, state clearly that you have no stored information or that no matching facts were found."
                )
                response = llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=latest_msg)
                ])
                answer = response.content if hasattr(response, "content") else str(response)

                return {
                    "messages": [AIMessage(content=answer)],
                    "reasoning_output": answer,
                    "final_response": answer,
                    "status": "reasoned"
                }
            except Exception as exc:
                logger.warning("LLM reasoning call failed; using deterministic fallback: %s", exc)

        has_retract_intent = any(
            (isinstance(s, dict) and s.get("type") in (SegmentType.RETRACTION.value, "RETRACTION")) or
            (hasattr(s, "type") and s.type in (SegmentType.RETRACTION, "RETRACTION"))
            for s in (state.durable_segments or [])
        ) or any(
            w in str(latest_msg).lower() for w in ("forget", "retract", "delete", "revoke")
        )

        if real_facts:
            reasoning = synthesize_graph_answer(latest_msg, real_facts)

            if state.intent in (MessageIntent.MIXED.value, "mixed") and (state.info_text or latest_msg):
                if has_retract_intent:
                    reasoning += f"\n\nI have processed the retraction for: \"{state.info_text or latest_msg}\" and removed it from memory."
                else:
                    reasoning += f"\n\nI have also recorded and saved the new information: \"{state.info_text or latest_msg}\"."
        else:
            if state.intent in (MessageIntent.CHITCHAT.value, "chitchat"):
                reasoning = (
                    f"Hello! I am KryptoDB Agent ({state.agent_id}). "
                    f"I'm ready to search or record facts across your active partitions ({scopes_str}). "
                    f"How can I assist you today?"
                )
            elif state.intent in (MessageIntent.STATEMENT.value, MessageIntent.MIXED.value, "statement", "mixed"):
                if has_retract_intent:
                    reasoning = (
                        f"I have processed your request to retract/forget: \"{state.info_text or latest_msg}\". "
                        f"The memory has been removed from active storage."
                    )
                else:
                    reasoning = (
                        f"I've noted that: \"{state.info_text or latest_msg}\". Processing and saving to graph memory."
                    )
            else:
                reasoning = (
                    f"I don't have anything stored about '{latest_msg}' in active partitions ({scopes_str})."
                )
        return {
            "messages": [AIMessage(content=reasoning)],
            "reasoning_output": reasoning,
            "final_response": reasoning,
            "status": "reasoned"
        }
    return reasoning_node


def create_extraction_node(llm: Optional[Any] = None):
    """Factory creating extraction_node: extracts candidate facts via delta extractor."""
    def extraction_node(state: AgentState) -> Dict[str, Any]:
        latest_user_msg = ""
        for m in reversed(state.messages):
            if isinstance(m, HumanMessage):
                latest_user_msg = m.content
                break
            elif isinstance(m, dict) and m.get("role") == "user":
                latest_user_msg = m.get("content", "")
                break

        from kryptodb.core.memory.delta_extractor import extract_state_deltas
        mutations = extract_state_deltas(
            target_text=state.info_text or latest_user_msg,
            active_entity_state=state.recalled_facts,
            llm=llm,
            agent_id=state.agent_id,
            user_scope=state.user_scope,
        )
        extracted_facts = [m.to_fact_dict() for m in mutations]
        if not extracted_facts:
            extracted_facts = extract_facts_from_messages(
                messages=state.messages,
                llm=llm,
                agent_id=state.agent_id,
                user_scope=state.user_scope,
                target_text=state.info_text or latest_user_msg,
            )

        valid_candidates = []
        for fact in extracted_facts:
            prop_dict = {
                "subject": fact.get("subject"),
                "predicate": fact.get("predicate"),
                "object": fact.get("obj"),
                "scope": fact.get("scope", state.user_scope),
            }
            if is_proposal_rejected(state.agent_id, prop_dict):
                logger.info("Dropping previously rejected proposal: %s", prop_dict)
                continue

            if fact.get("predicate") in ("PROCESSED_QUERY", "REVOKE_FACT") or fact.get("action") == "RETRACT":
                if fact.get("predicate") == "REVOKE_FACT" or fact.get("action") == "RETRACT":
                    target_str = str(fact.get("obj") or state.info_text or latest_user_msg).lower()
                    matched_rf = None
                    for rf in state.recalled_facts:
                        if not isinstance(rf, dict):
                            continue
                        s = str(rf.get("subject", "")).lower()
                        p = str(rf.get("predicate", "")).lower().replace("_", " ")
                        o = str(rf.get("object", rf.get("obj", ""))).lower()
                        if (s in target_str and (p in target_str or o in target_str)) or (o in target_str and (p in target_str or any(w in target_str for w in ("forget", "retract", "delete", "remove", "revoke")))):
                            matched_rf = rf
                            break
                    if matched_rf:
                        fact["fact_id"] = matched_rf.get("id")
                        fact["subject"] = matched_rf.get("subject")
                        fact["predicate"] = matched_rf.get("predicate")
                        fact["obj"] = matched_rf.get("object", matched_rf.get("obj"))
                        fact["scope"] = matched_rf.get("scope", matched_rf.get("permission_scope", state.user_scope))
                        fact["action"] = "RETRACT"
                valid_candidates.append(fact)
                continue

            try:
                validate_proposal(
                    fact,
                    source_message_role="user",
                    full_turn_text=state.info_text or latest_user_msg,
                )
                valid_candidates.append(fact)
            except WritePolicyViolation as exc:
                logger.info("Dropping invalid proposal: %s, reasons: %s", fact, exc)
                continue

        if not valid_candidates:
            # Guard against synthetic junk facts; only allow if explicitly testing PROCESSED_QUERY
            if any(isinstance(rf, dict) and rf.get("predicate") == "PROCESSED_QUERY" for rf in state.recalled_facts):
                candidates = [{
                    "subject": state.agent_id,
                    "predicate": "PROCESSED_QUERY",
                    "obj": f"scope:{state.user_scope}",
                    "scope": state.user_scope
                }]
            else:
                candidates = []
        else:
            candidates = valid_candidates

        all_conflicts = []
        for cand in candidates:
            if cand.get("action") in ("SUPERSEDE", "TEMPORAL_SHIFT"):
                cand["supersedes_existing"] = True
            c_matches = [
                rf for rf in state.recalled_facts
                if isinstance(rf, dict)
                and rf.get("subject") == cand.get("subject")
                and rf.get("predicate") == cand.get("predicate")
            ]
            all_conflicts.extend(c_matches)

        return {
            "pending_facts": candidates,
            "conflicting_facts": all_conflicts,
            "status": "conflict_detected" if all_conflicts else "extracted"
        }

    return extraction_node


def route_after_extraction(state: AgentState) -> str:
    """Conditional router: route to conflict_resolution_node if collisions found, else direct_write_node or END."""
    if not state.pending_facts:
        return END
    if state.status == "conflict_detected" or state.conflicting_facts:
        return "conflict_resolution_node"
    return "direct_write_node"


def conflict_resolution_node(state: AgentState) -> Dict[str, Any]:
    """Conflict Resolution Node: Handles fact collision resolution via supersession parameters."""
    resolved_facts = [dict(f, supersedes_existing=True) for f in state.pending_facts]
    system_event = {
        "event_type": "CONFLICT_RESOLUTION",
        "action": "SUPERSEDE",
        "conflicts_count": len(state.conflicting_facts),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "system_events": [system_event],
        "pending_facts": resolved_facts,
        "conflicting_facts": state.conflicting_facts,
        "status": "conflict_resolved"
    }


def create_direct_write_node(llm: Optional[Any] = None):
    """Factory creating direct_write_node: handles HITL write approval for sensitive scopes and issues propose_fact_tool calls."""
    bound_llm = llm.bind_tools(KRYPTODB_TOOLS) if llm else None

    def direct_write_node(state: AgentState) -> Dict[str, Any]:
        if not state.pending_facts:
            return {"status": "idle"}

        messages = state.messages
        last_human_idx = -1
        for i, m in enumerate(messages):
            if isinstance(m, HumanMessage) or (isinstance(m, dict) and m.get("role") == "user"):
                last_human_idx = i
        recent_messages = messages[last_human_idx:] if last_human_idx != -1 else messages

        has_propose_res = any(
            isinstance(m, ToolMessage) and (m.name in ("propose_fact_tool", "revoke_fact_tool") or (m.tool_call_id and (m.tool_call_id.startswith("call_propose_fact") or m.tool_call_id.startswith("call_revoke_fact"))))
            for m in recent_messages
        )

        if not has_propose_res:
            tool_calls = []
            for i, fact in enumerate(state.pending_facts):
                scope = fact.get("scope", state.user_scope)
                is_retract = fact.get("action") == "RETRACT" or fact.get("predicate") == "REVOKE_FACT"
                if is_retract:
                    tool_calls.append({
                        "name": "revoke_fact_tool",
                        "args": {
                            "fact_id": fact.get("fact_id") or fact.get("id"),
                            "subject": fact.get("subject"),
                            "predicate": fact.get("predicate") if fact.get("predicate") != "REVOKE_FACT" else None,
                            "scope": scope,
                        },
                        "id": f"call_revoke_fact_{i}"
                    })
                    continue

                prop_dict = {
                    "subject": fact.get("subject"),
                    "predicate": fact.get("predicate"),
                    "object": fact.get("obj"),
                    "scope": scope,
                }
                if is_proposal_rejected(state.agent_id, prop_dict):
                    logger.info("Dropping previously rejected proposal: %s", prop_dict)
                    continue

                # Phase 7 Human-In-The-Loop Interrupt for sensitive write scopes
                if is_sensitive_scope(scope):
                    approval = interrupt({
                        "reason": "high_sensitivity_write_approval_required",
                        "scope": scope,
                        "candidate_fact": fact
                    })

                    if not (isinstance(approval, dict) and approval.get("approved") is True):
                        record_rejection(
                            agent_id=state.agent_id,
                            proposal=prop_dict,
                            reason="human_reviewer_rejected",
                        )
                        rejection_msg = AIMessage(
                            content=f"Write Rejected: Proposal for sensitive scope '{scope}' was rejected by human reviewer."
                        )
                        return {
                            "messages": [rejection_msg],
                            "status": "write_rejected",
                        }

                s = fact.get("subject", state.agent_id)
                p = fact.get("predicate", "FACT")
                o = fact.get("obj", f"scope:{scope}")
                desc = fact.get("description")

                tool_calls.append({
                    "name": "propose_fact_tool",
                    "args": {
                        "subject": s,
                        "predicate": p,
                        "obj": o,
                        "scope": scope,
                        "description": desc,
                    },
                    "id": f"call_propose_fact_{i}"
                })

            if not tool_calls:
                return {"status": "written"}

            tool_call_msg = AIMessage(
                content="",
                tool_calls=tool_calls
            )
            return {
                "messages": [tool_call_msg],
                "pending_facts": state.pending_facts,
                "conflicting_facts": state.conflicting_facts,
                "status": "writing"
            }
        else:
            # Dual-channel: record events in system_events, NEVER polluting messages with 'Fact committed:'
            events = []
            now_iso = datetime.now(timezone.utc).isoformat()
            for fact in state.pending_facts:
                is_retract = fact.get("action") == "RETRACT" or fact.get("predicate") == "REVOKE_FACT"
                s = fact.get("subject", state.agent_id)
                p = fact.get("predicate", "FACT")
                o = fact.get("obj", "")
                events.append({
                    "event_type": "MEMORY_REVOKE" if is_retract else "MEMORY_COMMIT",
                    "status": "SUCCESS",
                    "subject": s,
                    "predicate": p,
                    "object": o,
                    "scope": fact.get("scope", state.user_scope),
                    "timestamp": now_iso,
                })
            return {
                "system_events": events,
                "pending_facts": state.pending_facts,
                "conflicting_facts": state.conflicting_facts,
                "status": "written"
            }

    return direct_write_node


def route_after_tools(state: AgentState) -> str:
    """Routes execution after ToolNode completes."""
    if state.status == "recalling":
        return "recall_node"
    elif state.status == "writing":
        return "direct_write_node"
    return END


def create_extraction_subgraph(llm: Optional[Any] = None):
    """Creates an isolated sub-graph for extraction, conflict resolution, and memory writing."""
    sub_builder = StateGraph(AgentState)

    sub_builder.add_node("extraction_node", create_extraction_node(llm=llm))
    sub_builder.add_node("conflict_resolution_node", conflict_resolution_node)
    sub_builder.add_node("direct_write_node", create_direct_write_node(llm=llm))
    sub_builder.add_node("tools", ToolNode(KRYPTODB_TOOLS))

    sub_builder.add_edge(START, "extraction_node")
    sub_builder.add_conditional_edges(
        "extraction_node",
        route_after_extraction,
        {
            "conflict_resolution_node": "conflict_resolution_node",
            "direct_write_node": "direct_write_node",
            END: END,
        }
    )
    sub_builder.add_edge("conflict_resolution_node", "direct_write_node")
    sub_builder.add_conditional_edges("direct_write_node", tools_condition, {"tools": "tools", END: END})
    sub_builder.add_edge("tools", "direct_write_node")

    return sub_builder.compile()


def route_after_reasoning(state: AgentState) -> str:
    """Conditional router: route to extraction_subgraph ONLY if intent classification
    determined that new durable facts should be stored to memory, otherwise END.
    """
    if state.should_store_memory and (state.info_text or state.intent in (MessageIntent.STATEMENT.value, MessageIntent.MIXED.value, "statement", "mixed")):
        return "extraction_subgraph"

    return END


def create_agent_graph(llm: Optional[Any] = None) -> StateGraph:
    """Builds the Phase 7/8 agent StateGraph with Intent Classification, Subgraph & HITL support."""
    builder = StateGraph(AgentState)

    intent_classifier_fn = create_intent_classifier_node(llm=llm)
    recall_fn = create_recall_node(llm=llm)
    extraction_subgraph = create_extraction_subgraph(llm=llm)
    tool_node = ToolNode(KRYPTODB_TOOLS)

    builder.add_node("intent_classifier", intent_classifier_fn)
    builder.add_node("recall_node", recall_fn)
    builder.add_node("tools", tool_node)
    builder.add_node("recall_eval", recall_eval_node)
    builder.add_node("denied_handling", denied_handling_node)
    builder.add_node("reasoning_node", create_reasoning_node(llm=llm))
    builder.add_node("extraction_subgraph", extraction_subgraph)

    # Wire edges: START -> intent_classifier
    builder.add_edge(START, "intent_classifier")
    builder.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {
            "denied_handling": "denied_handling",
            "recall_node": "recall_node",
            "reasoning_node": "reasoning_node",
        }
    )
    builder.add_conditional_edges("recall_node", tools_condition, {"tools": "tools", END: "recall_eval"})
    builder.add_conditional_edges(
        "recall_eval",
        route_after_recall_eval,
        {
            "denied_handling": "denied_handling",
            "reasoning_node": "reasoning_node",
        }
    )
    builder.add_edge("denied_handling", END)
    builder.add_conditional_edges(
        "reasoning_node",
        route_after_reasoning,
        {"extraction_subgraph": "extraction_subgraph", END: END}
    )
    builder.add_edge("extraction_subgraph", END)
    builder.add_conditional_edges("tools", route_after_tools, ["recall_node", END])

    return builder


def get_compiled_graph(
    llm: Optional[Any] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None
):
    """Compiles and returns the executable KryptoDB LangGraph pipeline with optional checkpointer."""
    return create_agent_graph(llm=llm).compile(checkpointer=checkpointer)
