"""REST routes under /v1 satisfying the unified GraphRAG API specification."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from kryptodb.core.context import RequestContext
from kryptodb.core.episodic.events import record_event
from kryptodb.core.episodic.summaries import summarize_session
from kryptodb.core.memory.memory_service import memory_service
from kryptodb.core.memory.models import MemoryRecord
from kryptodb.core.retrieval.ranking import AttributedMemoryResult
from kryptodb.core.retrieval.service import retrieval_service
from kryptodb.infrastructure.audit_ledger import audit_ledger
from kryptodb.interfaces.http.middleware.context import authorize_or_403, get_request_context

v1_router = APIRouter(prefix="/v1", tags=["V1 GraphRAG Memory"])


class ProposeMemoryRequest(BaseModel):
    subject: str
    predicate: str
    object: Optional[str] = None
    target: Optional[str] = None
    description: Optional[str] = None
    scope: str = "default"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_event_id: Optional[str] = None
    quote_substring: Optional[str] = None
    session_id: Optional[str] = None


class SearchMemoryRequest(BaseModel):
    query: str
    max_hops: int = Field(default=2, ge=1, le=2)
    top_k: int = Field(default=10, ge=1, le=50)
    scopes: Optional[List[str]] = None


class CorrectMemoryRequest(BaseModel):
    new_value: str


class RecordEventRequest(BaseModel):
    session_id: str
    role: str
    content: str
    scope: str = "default"


@v1_router.post("/memory/propose", response_model=MemoryRecord)
async def propose_memory_route(
    req: ProposeMemoryRequest,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(
        context,
        "memory:write",
        target_scopes=[req.scope],
        audit_ledger=audit_ledger,
    )
    target_val = req.target or req.object
    if not target_val:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either 'target' or 'object' must be provided.")
    try:
        rec = memory_service.propose_memory(
            subject=req.subject,
            predicate=req.predicate,
            obj=target_val,
            context=context,
            confidence=req.confidence,
            description=req.description,
            scope=req.scope,
            source_event_id=req.source_event_id,
            quote_substring=req.quote_substring,
            session_id=req.session_id,
        )
        audit_ledger.append(
            actor=context.agent_id,
            action="memory:write",
            tenant_id=context.tenant_id,
            scope=req.scope,
            outcome="allowed",
            target_id=rec.id,
        )
        return rec
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@v1_router.post("/memory/search", response_model=List[AttributedMemoryResult])
async def search_memory_route(
    req: SearchMemoryRequest,
    context: RequestContext = Depends(get_request_context),
):
    effective_scopes = authorize_or_403(
        context,
        "memory:read",
        target_scopes=req.scopes,
        audit_ledger=audit_ledger,
    )
    return retrieval_service.search(
        query=req.query,
        tenant_id=context.tenant_id,
        active_scopes=effective_scopes,
        top_k=req.top_k,
        max_hops=req.max_hops,
        now=context.now,
    )


@v1_router.get("/memory", response_model=List[MemoryRecord])
async def list_memories_route(
    limit: int = 50,
    offset: int = 0,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:read", audit_ledger=audit_ledger)
    return memory_service.list_memories(context=context, limit=limit, offset=offset)


@v1_router.get("/memory/{id}", response_model=MemoryRecord)
async def get_memory_route(
    id: str,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:read", audit_ledger=audit_ledger)
    mem = memory_service.get_memory(id=id, context=context)
    if not mem:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{id}' not found.")
    return mem


@v1_router.patch("/memory/{id}", response_model=MemoryRecord)
async def correct_memory_route(
    id: str,
    req: CorrectMemoryRequest,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:write", audit_ledger=audit_ledger)
    try:
        return memory_service.correct_memory(id=id, new_value=req.new_value, context=context)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@v1_router.delete("/memory/{id}")
async def forget_memory_route(
    id: str,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:revoke", audit_ledger=audit_ledger)
    memory_service.forget_memory(id=id, context=context)
    audit_ledger.append(
        actor=context.agent_id,
        action="memory:revoke",
        tenant_id=context.tenant_id,
        scope=",".join(sorted(context.active_scopes)),
        outcome="allowed",
        target_id=id,
    )
    return {"status": "forgotten", "id": id}


@v1_router.delete("/users/{user_id}/memories")
async def forget_user_route(
    user_id: str,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:revoke", audit_ledger=audit_ledger)
    count = memory_service.forget_user(user_id=user_id, context=context)
    return {"status": "user_forgotten", "user_id": user_id, "revoked_count": count}


@v1_router.post("/events")
async def record_event_route(
    req: RecordEventRequest,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:write", target_scopes=[req.scope], audit_ledger=audit_ledger)
    if not memory_service.event_store:
        raise HTTPException(status_code=500, detail="Event store not configured")
    ev = record_event(
        session_id=req.session_id,
        role=req.role,
        content=req.content,
        context=context,
        store=memory_service.event_store,
        scope=req.scope,
    )
    return {"id": ev.id, "session_id": ev.session_id, "role": ev.event_type}


@v1_router.post("/sessions/{session_id}/summarize")
async def summarize_session_route(
    session_id: str,
    context: RequestContext = Depends(get_request_context),
):
    authorize_or_403(context, "memory:read", audit_ledger=audit_ledger)
    if not memory_service.event_store:
        raise HTTPException(status_code=500, detail="Event store not configured")
    summary = summarize_session(session_id=session_id, context=context, store=memory_service.event_store)
    return {
        "id": summary.id,
        "session_id": summary.session_id,
        "summary": summary.summary,
        "topics": summary.topics,
    }
