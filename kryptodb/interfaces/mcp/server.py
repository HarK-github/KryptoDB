"""KryptoDB FastMCP Server providing unified tools for AI agents."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
from fastmcp import FastMCP

from kryptodb.core.context import RequestContext
from kryptodb.core.episodic.events import record_event as core_record_event
from kryptodb.core.identity import session_manager
from kryptodb.core.memory.memory_service import memory_service
from kryptodb.core.retrieval.service import retrieval_service

logger = logging.getLogger("kryptodb.interfaces.mcp")

mcp = FastMCP("KryptoDB")

DEFAULT_SESSION_ID = "mcp-default-session"


def _get_mcp_context(session_id: Optional[str] = None) -> RequestContext:
    """Builds RequestContext from active MCP session state."""
    sid = session_id or os.environ.get("KRYPTODB_SESSION_ID", DEFAULT_SESSION_ID)
    ctx_meta = session_manager.get_or_create(sid)
    return RequestContext.create(
        tenant_id=ctx_meta.tenant_id or "default",
        agent_id=ctx_meta.agent_id or "agent-1",
        session_id=sid,
        active_scopes=list(ctx_meta.active_scopes),
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
    """Proposes a new durable factual memory.
    
    Validates predicate cardinality, enforces provenance quotations, and resolves conflicts.
    """
    context = _get_mcp_context(session_id)
    try:
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
        return json.dumps({
            "status": record.status.value,
            "fact_id": record.id,
            "subject": record.subject,
            "predicate": record.predicate,
            "object": record.object,
            "version": record.version,
            "previous_version_id": record.previous_version_id,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})


@mcp.tool()
def query_memory(
    query: str,
    max_hops: int = 2,
    top_k: int = 5,
    session_id: Optional[str] = None,
) -> str:
    """Retrieves relevant facts and associations using hybrid vector + BM25 search,

    2-hop cycle-safe graph traversal, and 3-stage cross-encoder ranking with provenance.
    """
    context = _get_mcp_context(session_id)
    results = retrieval_service.search(
        query=query,
        tenant_id=context.tenant_id,
        active_scopes=list(context.active_scopes),
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


@mcp.tool()
def list_memories(
    limit: int = 20,
    offset: int = 0,
    session_id: Optional[str] = None,
) -> str:
    """Lists durable memories accessible within the active session scopes."""
    context = _get_mcp_context(session_id)
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


@mcp.tool()
def correct_memory(
    id: str,
    new_value: str,
    session_id: Optional[str] = None,
) -> str:
    """Corrects an existing memory fact, creating an incremented version and superseding the prior value."""
    context = _get_mcp_context(session_id)
    try:
        updated = memory_service.correct_memory(id=id, new_value=new_value, context=context)
        return json.dumps({
            "status": "corrected",
            "old_id": id,
            "new_id": updated.id,
            "version": updated.version,
            "object": updated.object,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "message": str(exc)})


@mcp.tool()
def forget_memory(
    id: str,
    session_id: Optional[str] = None,
) -> str:
    """Permanently revokes a memory fact from active retrieval."""
    context = _get_mcp_context(session_id)
    memory_service.forget_memory(id=id, context=context)
    return json.dumps({"status": "revoked", "id": id})


@mcp.tool()
def record_event(
    session_id: str,
    role: str,
    content: str,
    scope: str = "default",
) -> str:
    """Records an episodic conversational turn for provenance tracking."""
    context = _get_mcp_context(session_id)
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
    return query_memory(query=query or "", session_id=session_id)


@mcp.tool()
def session_activate(scopes: List[str]) -> str:
    """Activates specific memory scopes for the active session."""
    ctx = session_manager.activate_scopes(DEFAULT_SESSION_ID, scopes)
    return json.dumps({
        "session_id": ctx.session_id,
        "active_scopes": list(ctx.active_scopes),
    }, indent=2)


@mcp.tool()
def session_show() -> str:
    """Shows the currently active partitions and identity of the session."""
    ctx = session_manager.get_or_create(DEFAULT_SESSION_ID)
    return json.dumps({
        "session_id": ctx.session_id,
        "tenant_id": ctx.tenant_id,
        "agent_id": ctx.agent_id,
        "active_scopes": list(ctx.active_scopes),
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
