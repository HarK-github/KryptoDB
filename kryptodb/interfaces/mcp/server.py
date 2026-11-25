"""KryptoDB FastMCP Server enforcing unified Authenticator + PolicyEngine access control."""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional
try:
    from fastmcp import FastMCP
except ImportError:
    class FastMCP:  # type: ignore
        def __init__(self, name: str):
            self.name = name
        def tool(self):
            return lambda fn: fn
        def run(self):
            raise RuntimeError("fastmcp is required to run the MCP server")

from kryptodb.core.context import RequestContext
from kryptodb.core.episodic.events import record_event as core_record_event
from kryptodb.core.identity import authenticator, policy_engine, session_manager
from kryptodb.core.identity.policy import PolicyDeniedError
from kryptodb.core.memory.memory_service import memory_service
from kryptodb.core.retrieval.service import retrieval_service
from kryptodb.infrastructure.audit_ledger import audit_ledger

logger = logging.getLogger("kryptodb.interfaces.mcp")

mcp = FastMCP("KryptoDB")

DEFAULT_SESSION_ID = "mcp-default-session"


def _get_mcp_context(
    session_id: Optional[str] = None,
    api_key: Optional[str] = None,
) -> RequestContext:
    """Fail-closed RequestContext builder for MCP tools.

    Requires a valid KRYPTODB_API_KEY verified by Authenticator; never falls back
    to unauthenticated default tenant/agent identities.
    """
    raw_key = (api_key or os.environ.get("KRYPTODB_API_KEY") or "").strip()
    if not raw_key:
        raise PolicyDeniedError("KRYPTODB_API_KEY is required to invoke KryptoDB MCP tools")

    claims = authenticator.verify_token(raw_key)
    if not claims:
        raise PolicyDeniedError("Invalid or revoked KRYPTODB_API_KEY")

    sid = session_id or os.environ.get("KRYPTODB_SESSION_ID", DEFAULT_SESSION_ID)
    is_admin = bool(claims.get("is_admin", False))
    token_scopes = list(claims.get("scopes") or [])

    # Intersect any session-narrowed scopes against token scopes (never escalate)
    ctx_meta = session_manager.get_or_create(sid)
    session_scopes = list(getattr(ctx_meta, "active_scopes", []) or [])
    if session_scopes:
        if is_admin or "*" in token_scopes:
            effective_scopes = session_scopes
        else:
            narrowed = [s for s in session_scopes if s in token_scopes]
            effective_scopes = narrowed if narrowed else token_scopes
    else:
        effective_scopes = token_scopes

    return RequestContext.create(
        tenant_id=str(claims.get("tenant_id") or "default"),
        agent_id=str(claims.get("agent_id") or "agent-1"),
        session_id=sid,
        active_scopes=effective_scopes,
        is_system=is_admin or ("*" in token_scopes),
        role=str(claims.get("role") or ("admin" if is_admin else "writer")),
        permissions=list(claims.get("permissions") or []),
    )


@mcp.tool()
def propose_memory(
    subject: str,
    predicate: str,
    obj: str,
    description: Optional[str] = None,
    scope: str = "default",
    confidence: float = 1.0,
    source_event_id: Optional[str] = None,
    quote_substring: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Proposes a new durable factual memory."""
    try:
        context = _get_mcp_context(session_id)
        policy_engine.authorize(context, "memory:write", target_scopes=[scope], audit_ledger=audit_ledger)
        record = memory_service.propose_memory(
            subject=subject,
            predicate=predicate,
            obj=obj,
            context=context,
            confidence=confidence,
            description=description,
            scope=scope,
            source_event_id=source_event_id,
            quote_substring=quote_substring,
            session_id=session_id,
        )
        audit_ledger.append(
            actor=context.agent_id,
            action="mcp:propose_memory",
            tenant_id=context.tenant_id,
            scope=scope,
            outcome="allowed",
            target_id=record.id,
        )
        return json.dumps({
            "status": record.status.value,
            "fact_id": record.id,
            "subject": record.subject,
            "predicate": record.predicate,
            "object": record.object,
            "version": record.version,
            "previous_version_id": record.previous_version_id,
        }, indent=2)
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})


@mcp.tool()
def query_memory(
    query: str,
    max_hops: int = 2,
    top_k: int = 5,
    requested_scope: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Retrieves relevant facts using hybrid search + 2-hop graph traversal + reranking."""
    try:
        context = _get_mcp_context(session_id)
        effective_scopes = policy_engine.authorize(
            context,
            "memory:read",
            target_scopes=[requested_scope] if requested_scope else None,
            audit_ledger=audit_ledger,
        )
        results = retrieval_service.search(
            query=query,
            tenant_id=context.tenant_id,
            active_scopes=effective_scopes,
            top_k=top_k,
            max_hops=max_hops,
            now=context.now,
        )
        if not results:
            return "No relevant memories found."

        output = []
        for r in results:
            provenance = " -> ".join([f"({s.source})-[{s.predicate}]->({s.target})" for s in r.provenance_path])
            output.append({
                "summary": r.summary,
                "score": r.final_score,
                "hops": r.hops,
                "citation": r.citation_tag,
                "provenance_chain": provenance,
            })
        return json.dumps(output, indent=2)
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})


@mcp.tool()
def list_memories(
    limit: int = 20,
    offset: int = 0,
    session_id: Optional[str] = None,
) -> str:
    """Lists durable memories accessible within the authenticated token scopes."""
    try:
        context = _get_mcp_context(session_id)
        policy_engine.authorize(context, "memory:read", audit_ledger=audit_ledger)
        records = memory_service.list_memories(context=context, limit=limit, offset=offset)
        return json.dumps([
            {
                "id": r.id,
                "subject": r.subject,
                "predicate": r.predicate,
                "object": r.object,
                "scope": r.scope,
                "status": r.status.value,
                "version": r.version,
            }
            for r in records
        ], indent=2)
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})


@mcp.tool()
def correct_memory(
    id: str,
    new_value: str,
    session_id: Optional[str] = None,
) -> str:
    """Corrects an existing memory fact, creating an incremented version and superseding the prior value."""
    try:
        context = _get_mcp_context(session_id)
        policy_engine.authorize(context, "memory:write", audit_ledger=audit_ledger)
        updated = memory_service.correct_memory(id=id, new_value=new_value, context=context)
        return json.dumps({
            "status": "corrected",
            "old_id": id,
            "new_id": updated.id,
            "version": updated.version,
            "object": updated.object,
        }, indent=2)
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})


@mcp.tool()
def forget_memory(
    id: str,
    session_id: Optional[str] = None,
) -> str:
    """Permanently revokes a memory fact from active retrieval."""
    try:
        context = _get_mcp_context(session_id)
        policy_engine.authorize(context, "memory:revoke", audit_ledger=audit_ledger)
        memory_service.forget_memory(id=id, context=context)
        audit_ledger.append(
            actor=context.agent_id,
            action="mcp:forget_memory",
            tenant_id=context.tenant_id,
            scope=",".join(sorted(context.active_scopes)),
            outcome="allowed",
            target_id=id,
        )
        return json.dumps({"status": "revoked", "id": id})
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})


@mcp.tool()
def record_event(
    session_id: str,
    role: str,
    content: str,
    scope: str = "default",
) -> str:
    """Records an episodic conversational turn for provenance tracking."""
    try:
        context = _get_mcp_context(session_id)
        policy_engine.authorize(context, "memory:write", target_scopes=[scope], audit_ledger=audit_ledger)
        if not memory_service.event_store:
            return json.dumps({"status": "error", "message": "Event store not configured"})
        ev = core_record_event(
            session_id=session_id,
            role=role,
            content=content,
            context=context,
            store=memory_service.event_store,
            scope=scope,
        )
        return json.dumps({"status": "recorded", "event_id": ev.id, "session_id": ev.session_id})
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})


# Backward-compatible aliases
propose_fact = propose_memory


@mcp.tool()
def propose_fact_tool(
    subject: str,
    predicate: str,
    obj: str,
    description: Optional[str] = None,
    scope: str = "default",
    confidence: float = 1.0,
) -> str:
    """Alias for propose_memory."""
    return propose_memory(subject=subject, predicate=predicate, obj=obj, description=description, scope=scope, confidence=confidence)


@mcp.tool()
def remember(
    subject: str,
    predicate: str,
    obj: str,
    scope: str = "default",
    description: Optional[str] = None,
    dataset_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Legacy alias for propose_memory."""
    return propose_memory(subject=subject, predicate=predicate, obj=obj, description=description, scope=scope, session_id=session_id)


@mcp.tool()
def recall(
    query: Optional[str] = None,
    requested_scope: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Legacy alias for query_memory."""
    return query_memory(query=query or "", requested_scope=requested_scope, session_id=session_id)


@mcp.tool()
def session_activate(scopes: List[str]) -> str:
    """Narrows active session scopes within the authenticated token's allowed scopes."""
    try:
        context = _get_mcp_context(DEFAULT_SESSION_ID)
        allowed = policy_engine.authorize(context, "memory:read", target_scopes=scopes, audit_ledger=audit_ledger)
        ctx = session_manager.activate_scopes(DEFAULT_SESSION_ID, allowed or scopes)
        return json.dumps({
            "session_id": ctx.session_id,
            "active_scopes": list(ctx.active_scopes),
        }, indent=2)
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})


@mcp.tool()
def session_show() -> str:
    """Shows the authenticated identity and active scopes of the MCP session."""
    try:
        context = _get_mcp_context(DEFAULT_SESSION_ID)
        return json.dumps({
            "session_id": context.session_id,
            "tenant_id": context.tenant_id,
            "agent_id": context.agent_id,
            "role": context.role,
            "active_scopes": sorted(context.active_scopes),
        }, indent=2)
    except PolicyDeniedError as exc:
        return json.dumps({"status": "denied", "message": str(exc)})


if __name__ == "__main__":
    # Fail closed on startup if KRYPTODB_API_KEY is missing or invalid
    _get_mcp_context(DEFAULT_SESSION_ID)
    mcp.run()
