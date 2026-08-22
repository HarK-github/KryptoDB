"""
Phase 7 & 8: Human-In-The-Loop High-Sensitivity Write Gating & Production Durability.
Implements interrupt-based human approval for sensitive memory scopes (CONFIDENTIAL, RESTRICTED, TOP_SECRET)
and durable checkpointer support.
"""

import kryptodb
import logging
from typing import Annotated, Any, Dict, List, Optional, Union
import json
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import interrupt

from kryptodb.agent.tools import KRYPTODB_TOOLS, query_memory_tool, propose_fact_tool
from kryptodb.agent.store import KryptoDBStore
from kryptodb.agent.extractor import extract_facts_from_messages, FactItem

logger = logging.getLogger(__name__)


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


def is_sensitive_scope(scope: str) -> bool:
    """Checks if a scope tier requires human-in-the-loop approval before writing."""
    if not scope:
        return False
    upper = scope.upper()
    return upper.startswith("CONFIDENTIAL:") or upper.startswith("RESTRICTED:") or upper.startswith("TOP_SECRET:")


class AgentState(BaseModel):
    """Pydantic State Schema for the KryptoDB Agent Graph."""
    messages: Annotated[List[Any], add_messages] = Field(default_factory=list, description="Conversation messages and tool calls")
    user_scope: str = Field(default="AUTO", description="Authorization scope for memory queries/writes (default AUTO for autonomous routing)")
    agent_id: str = Field(default="agent-1", description="Identifier of the executing agent")
    recalled_facts: List[Dict[str, Any]] = Field(default_factory=list, description="Facts recalled from memory substrate")
    denied_recalls: List[Dict[str, Any]] = Field(default_factory=list, description="Access denials encountered during recall")
    pending_facts: List[Dict[str, Any]] = Field(default_factory=list, description="Extracted candidate facts awaiting write")
    conflicting_facts: List[Dict[str, Any]] = Field(default_factory=list, description="Colliding facts detected on same Subject+Predicate")
    reasoning_output: Optional[str] = Field(default=None, description="Output/thoughts from reasoning node")
    status: str = Field(default="initialized", description="Current execution state status")

    @field_validator("user_scope")
    @classmethod
    def validate_user_scope(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            return "AUTO"
        return v.strip()

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("agent_id must be a non-empty string")
        return v.strip()


def create_recall_node(llm: Optional[Any] = None, store: Optional[KryptoDBStore] = None):
    """Factory creating recall_node, using KryptoDBStore lookup or deterministic tool call fallback."""
    bound_llm = llm.bind_tools(KRYPTODB_TOOLS) if llm else None

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
                from kryptodb.memory_api.agents import get_agent
                agent = get_agent(state.agent_id)
                allowed_scopes = agent.get("allowed_scopes", []) if agent else ([state.user_scope] if state.user_scope and state.user_scope != "AUTO" else [])
            except Exception:
                allowed_scopes = [state.user_scope] if state.user_scope and state.user_scope != "AUTO" else []

            # Autonomous scope resolution: if AUTO or GLOBAL, match keywords or query across all authorized scopes
            if not state.user_scope or state.user_scope.upper() in ("AUTO", "GLOBAL", "*"):
                from kryptodb.agent.extractor import resolve_scope_for_content
                matched_scope = resolve_scope_for_content(latest_user_msg, agent_id=state.agent_id, default_scope="AUTO")
                req_scope = matched_scope if (matched_scope != "GLOBAL" and (not allowed_scopes or matched_scope in allowed_scopes)) else None
            else:
                req_scope = state.user_scope

            if bound_llm:
                try:
                    from langchain_core.messages import SystemMessage
                    scopes_desc = ", ".join(allowed_scopes) if allowed_scopes else "all authorized scopes"
                    system_msg = SystemMessage(
                        content=(
                            f"You are KryptoDB Agent '{state.agent_id}'. You have access to encrypted memory graph partitions: [{scopes_desc}].\n"
                            f"Call `query_memory_tool` to search for relevant memory facts across your authorized partitions before answering.\n"
                            f"CRITICAL: To ensure you find the necessary information, you SHOULD make multiple parallel tool calls to `query_memory_tool` using different wordings, synonyms, and sub-queries.\n"
                            f"You may specify `requested_scope` to target a specific partition, or pass None to search across all authorized partitions."
                        )
                    )
                    prompt_messages = [system_msg] + [m for m in messages if not isinstance(m, SystemMessage)]
                    response = bound_llm.invoke(prompt_messages)
                    if hasattr(response, "tool_calls") and response.tool_calls:
                        return {"messages": [response], "status": "recalling", "recalled_facts": [], "denied_recalls": []}
                except Exception as exc:
                    logger.warning("LLM recall/tool-selection call failed: %s", exc)

            tool_call_msg = AIMessage(
                content="",
                tool_calls=[{
                    "name": "query_memory_tool",
                    "args": {
                        "query": latest_user_msg,
                        "requested_scope": req_scope,
                        "mode": "local"
                    },
                    "id": "call_recall_query"
                }]
            )
            return {"messages": [tool_call_msg], "status": "recalling", "recalled_facts": [], "denied_recalls": []}
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
                            continue
                        elif "facts" in payload:
                            parsed_facts.extend(payload["facts"])
                        else:
                            parsed_facts.append(payload)
                except Exception:
                    continue

            return {
                "recalled_facts": _dedupe_facts(parsed_facts),
                "denied_recalls": denials,
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


def create_reasoning_node(llm: Optional[Any] = None):
    """Factory creating reasoning_node: uses LLM if present to synthesize dynamic response or fallback reasoning."""
    def reasoning_node(state: AgentState) -> Dict[str, Any]:
        recalled = state.recalled_facts

        latest_msg = "No input"
        for m in reversed(state.messages):
            if isinstance(m, HumanMessage):
                latest_msg = m.content
                break
            elif isinstance(m, dict) and m.get("role") == "user":
                latest_msg = m.get("content", "")
                break

        try:
            from kryptodb.memory_api.agents import get_agent
            agent = get_agent(state.agent_id)
            allowed_scopes = agent.get("allowed_scopes", []) if agent else ([state.user_scope] if state.user_scope and state.user_scope != "AUTO" else [])
        except Exception:
            allowed_scopes = [state.user_scope] if state.user_scope and state.user_scope != "AUTO" else []
        scopes_str = ", ".join(allowed_scopes) if allowed_scopes else "authorized knowledge partitions"

        if llm and recalled and len(recalled) > 0:
            try:
                system_prompt = (
                    f"You are KryptoDB Agent ({state.agent_id}), an assistant whose factual source is KryptoDB's partition-encrypted knowledge graph.\n"
                    f"Your authorized knowledge partitions: [{scopes_str}].\n"
                    f"Retrieved graph facts: {json.dumps(recalled)}.\n\n"
                    "Grounding rules (mandatory):\n"
                    "- Answer the user's question accurately using the retrieved graph facts.\n"
                    "- Mention relationships and source scopes when relevant.\n"
                    "- Do not use general world knowledge or hallucinate details not in the graph facts.\n"
                    "- If no facts answer the question, state that the requested information was not found in your accessible knowledge graphs."
                )
                from langchain_core.messages import SystemMessage
                response = llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=latest_msg)
                ])
                answer = response.content if hasattr(response, "content") else str(response)

                return {
                    "messages": [AIMessage(content=answer)],
                    "reasoning_output": answer,
                    "status": "reasoned"
                }
            except Exception as exc:
                logger.warning("LLM reasoning call failed; using deterministic fallback: %s", exc)

        if recalled:
            formatted_facts = []
            for f in recalled[:6]:
                s = f.get("subject", "")
                p = f.get("predicate", "")
                o = f.get("object", "")
                scope = f.get("permission_scope", "authorized")
                formatted_facts.append(f"• {s} — {p} → {o} ({scope})")
            facts_summary = "\n".join(formatted_facts)
            reasoning = (
                f"Recalled facts from active knowledge graph:\n{facts_summary}"
            )
        else:
            clean_msg = latest_msg.strip().lower()
            greetings = {"hi", "hello", "hey", "hi hih hi", "greetings", "howdy", "hola", "help", "hi!", "hello!"}
            is_greeting = any(clean_msg.startswith(g) for g in ["hi", "hello", "hey", "greetings", "howdy", "hola", "help"]) or clean_msg in greetings
            if is_greeting:
                reasoning = (
                    f"Hello! I am KryptoDB Agent ({state.agent_id}). "
                    f"I'm ready to search or record facts across your active partitions ({scopes_str}). "
                    f"How can I assist you today?"
                )
            else:
                reasoning = (
                    f"No matching memory facts were found for your query in active partitions ({scopes_str}). "
                    f"You can store a new fact by typing: 'Store fact: [subject], [predicate], [object]'."
                )
        return {
            "messages": [AIMessage(content=reasoning)],
            "reasoning_output": reasoning,
            "status": "reasoned"
        }
    return reasoning_node


def create_extraction_node(llm: Optional[Any] = None):
    """Factory creating extraction_node: extracts candidate facts via Trustcall/Pydantic schema extractor."""
    def extraction_node(state: AgentState) -> Dict[str, Any]:
        extracted_facts = extract_facts_from_messages(
            messages=state.messages,
            llm=llm,
            agent_id=state.agent_id,
            user_scope=state.user_scope
        )

        candidate = extracted_facts[0] if extracted_facts else {
            "subject": state.agent_id,
            "predicate": "PROCESSED_QUERY",
            "obj": f"scope:{state.user_scope}",
            "scope": state.user_scope
        }

        conflicts = [
            rf for rf in state.recalled_facts
            if isinstance(rf, dict)
            and rf.get("subject") == candidate.get("subject")
            and rf.get("predicate") == candidate.get("predicate")
        ]

        if conflicts:
            return {
                "pending_facts": [candidate],
                "conflicting_facts": conflicts,
                "status": "conflict_detected"
            }

        return {
            "pending_facts": [candidate],
            "status": "extracted"
        }

    return extraction_node


def route_after_extraction(state: AgentState) -> str:
    """Conditional router: route to conflict_resolution_node if collisions found, else direct_write_node."""
    if state.status == "conflict_detected" or state.conflicting_facts:
        return "conflict_resolution_node"
    return "direct_write_node"


def conflict_resolution_node(state: AgentState) -> Dict[str, Any]:
    """Conflict Resolution Node: Handles fact collision resolution via supersession parameters."""
    resolved_facts = [dict(f, supersedes_existing=True) for f in state.pending_facts]
    notice_msg = AIMessage(
        content=f"Conflict Resolution: Superseding {len(state.conflicting_facts)} existing fact(s) on subject/predicate collision."
    )
    return {
        "messages": [notice_msg],
        "pending_facts": resolved_facts,
        "status": "conflict_resolved"
    }


def create_direct_write_node(llm: Optional[Any] = None, store: Optional[KryptoDBStore] = None):
    """Factory creating direct_write_node: handles HITL write approval for sensitive scopes and issues propose_fact_tool calls."""
    bound_llm = llm.bind_tools(KRYPTODB_TOOLS) if llm else None

    def direct_write_node(state: AgentState) -> Dict[str, Any]:
        messages = state.messages
        has_propose_res = any(isinstance(m, ToolMessage) and (m.name == "propose_fact_tool" or m.tool_call_id == "call_propose_fact") for m in messages)

        if not has_propose_res:
            fact = state.pending_facts[0] if state.pending_facts else {
                "subject": state.agent_id,
                "predicate": "PROCESSED_QUERY",
                "obj": f"scope:{state.user_scope}",
                "scope": state.user_scope
            }

            scope = fact.get("scope", state.user_scope)

            # Phase 7 Human-In-The-Loop Interrupt for sensitive write scopes
            if is_sensitive_scope(scope):
                approval = interrupt({
                    "reason": "high_sensitivity_write_approval_required",
                    "scope": scope,
                    "candidate_fact": fact
                })

                if not (isinstance(approval, dict) and approval.get("approved") is True):
                    rejection_msg = AIMessage(
                        content=f"Write Rejected: Candidate fact write for high-sensitivity scope '{scope}' was rejected by human reviewer."
                    )
                    return {
                        "messages": [rejection_msg],
                        "status": "write_rejected"
                    }

            if store is not None:
                ns = KryptoDBStore.format_namespace(scope, fact.get("subject", state.agent_id))
                store.put(ns, f"fact_{fact.get('predicate')}", fact)

            try:
                import uuid
                from kryptodb.memory_api.propose import propose_fact
                from kryptodb.graph.schema import Edge
                edge = Edge(
                    id=str(uuid.uuid4()),
                    type="FACT",
                    subject_id=fact.get("subject", state.agent_id),
                    predicate=fact.get("predicate", "STORED_FACT"),
                    object_id=fact.get("obj", f"scope:{scope}"),
                    permission_scope=scope,
                    trust_weight=0.9,
                    source_agent_id=state.agent_id
                )
                propose_result = propose_fact(state.agent_id, edge, False, [])
                if isinstance(propose_result, dict) and propose_result.get("status") not in ("admit", "ok", "queued"):
                    raise RuntimeError(f"Propose rejected: {propose_result}")
            except Exception as exc:
                logger.warning("Direct fact propose failed: %s", exc)
                fail_msg = AIMessage(content="Memory write failed due to internal error.")
                return {
                    "messages": [fail_msg],
                    "status": "write_failed"
                }

            s = fact.get("subject", state.agent_id)
            p = fact.get("predicate", "FACT")
            o = fact.get("obj", "")
            confirm_text = f"Successfully stored new fact to knowledge graph partition ({scope}): {s} — {p} → {o}"
            confirm_msg = AIMessage(content=confirm_text)
            return {
                "messages": [confirm_msg],
                "reasoning_output": confirm_text,
                "pending_facts": [fact],
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


def create_extraction_subgraph(llm: Optional[Any] = None, store: Optional[KryptoDBStore] = None):
    """Creates an isolated sub-graph for extraction, conflict resolution, and memory writing."""
    sub_builder = StateGraph(AgentState)

    sub_builder.add_node("extraction_node", create_extraction_node(llm=llm))
    sub_builder.add_node("conflict_resolution_node", conflict_resolution_node)
    sub_builder.add_node("direct_write_node", create_direct_write_node(llm=llm, store=store))
    sub_builder.add_node("tools", ToolNode(KRYPTODB_TOOLS))

    sub_builder.add_edge(START, "extraction_node")
    sub_builder.add_conditional_edges(
        "extraction_node",
        route_after_extraction,
        {"conflict_resolution_node": "conflict_resolution_node", "direct_write_node": "direct_write_node"}
    )
    sub_builder.add_edge("conflict_resolution_node", "direct_write_node")
    sub_builder.add_conditional_edges("direct_write_node", tools_condition, {"tools": "tools", END: END})
    sub_builder.add_edge("tools", "direct_write_node")

    return sub_builder.compile()


def route_after_reasoning(state: AgentState) -> str:
    """Conditional router: route to extraction_subgraph for store/add requests or sensitive scopes, else END."""
    latest_msg = ""
    for m in reversed(state.messages):
        if isinstance(m, HumanMessage):
            latest_msg = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            latest_msg = m.get("content", "")
            break
    clean = latest_msg.strip().lower()
    if is_sensitive_scope(state.user_scope) or clean.startswith(("store fact", "add fact", "fact:", "store:", "add:", "persist", "scan", "retrieve", "malware sample")):
        return "extraction_subgraph"
    return END


def create_agent_graph(llm: Optional[Any] = None, store: Optional[KryptoDBStore] = None) -> StateGraph:
    """Builds the Phase 7/8 agent StateGraph with Subgraph & HITL support."""
    builder = StateGraph(AgentState)

    recall_fn = create_recall_node(llm=llm, store=store)
    extraction_subgraph = create_extraction_subgraph(llm=llm, store=store)
    tool_node = ToolNode(KRYPTODB_TOOLS)

    builder.add_node("recall_node", recall_fn)
    builder.add_node("tools", tool_node)
    builder.add_node("recall_eval", recall_eval_node)
    builder.add_node("denied_handling", denied_handling_node)
    builder.add_node("reasoning_node", create_reasoning_node(llm=llm))
    builder.add_node("extraction_subgraph", extraction_subgraph)

    # Wire edges
    builder.add_edge(START, "recall_node")
    builder.add_conditional_edges("recall_node", tools_condition, {"tools": "tools", END: "recall_eval"})
    builder.add_conditional_edges(
        "recall_eval",
        route_after_recall_eval,
        {"denied_handling": "denied_handling", "reasoning_node": "reasoning_node", "extraction_subgraph": "extraction_subgraph"}
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
    store: Optional[KryptoDBStore] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None
):
    """Compiles and returns the executable KryptoDB LangGraph pipeline with optional checkpointer."""
    return create_agent_graph(llm=llm, store=store).compile(checkpointer=checkpointer)
