from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kryptodb.core.context import RequestContext
from kryptodb.core.memory import MemoryRecord, memory_service
from kryptodb.core.retrieval import AttributedMemoryResult, retrieval_service
from kryptodb.infrastructure.audit_ledger import audit_ledger
from kryptodb.interfaces.http.middleware.context import authorize_or_403, require_context

router = APIRouter(prefix="/api/v1/memory", tags=["Memory"])


class SearchRequest(BaseModel):
    query: str
    max_hops: int = Field(default=2, ge=1, le=2)
    top_k: int = Field(default=10, ge=1, le=50)
    scopes: Optional[List[str]] = None


class WriteFactRequest(BaseModel):
    subject: str
    predicate: str
    object: str
    description: Optional[str] = None
    scope: str = "default"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    dataset_id: Optional[str] = None


@router.post("/search", response_model=List[AttributedMemoryResult])
async def search_memory(
    req: SearchRequest,
    context: RequestContext = Depends(require_context),
):
    effective_scopes = authorize_or_403(
        context,
        "memory:read",
        target_scopes=req.scopes,
        audit_ledger=audit_ledger,
    )
    results = retrieval_service.search(
        query=req.query,
        tenant_id=context.tenant_id,
        active_scopes=effective_scopes,
        top_k=req.top_k,
        max_hops=req.max_hops,
        now=context.now,
    )
    return results


@router.post("/write", response_model=MemoryRecord)
async def write_fact(
    req: WriteFactRequest,
    context: RequestContext = Depends(require_context),
):
    authorize_or_403(
        context,
        "memory:write",
        target_scopes=[req.scope],
        audit_ledger=audit_ledger,
    )
    record = memory_service.write_fact(
        subject=req.subject,
        predicate=req.predicate,
        obj=req.object,
        description=req.description,
        scope=req.scope,
        tenant_id=context.tenant_id,
        confidence=req.confidence,
        source_agent_id=context.agent_id,
        dataset_id=req.dataset_id,
    )
    audit_ledger.append(
        actor=context.agent_id,
        action="memory:write",
        tenant_id=context.tenant_id,
        scope=req.scope,
        outcome="allowed",
        target_id=record.id,
    )
    return record


@router.delete("/revoke/{fact_id}")
async def revoke_fact(
    fact_id: str,
    context: RequestContext = Depends(require_context),
):
    authorize_or_403(
        context,
        "memory:revoke",
        audit_ledger=audit_ledger,
    )
    success = memory_service.revoke_fact(
        fact_id=fact_id,
        tenant_id=context.tenant_id,
        context=context,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Fact not found or not authorized in active scopes")
    audit_ledger.append(
        actor=context.agent_id,
        action="memory:revoke",
        tenant_id=context.tenant_id,
        scope=",".join(sorted(context.active_scopes)),
        outcome="allowed",
        target_id=fact_id,
    )
    return {"status": "revoked", "fact_id": fact_id}


@router.get("/ontology")
async def get_ontology(context: RequestContext = Depends(require_context)):
    authorize_or_403(context, "memory:read", audit_ledger=audit_ledger)
    from kryptodb.core.memory.predicates import predicate_registry

    predicates = predicate_registry.list_predicates()
    lines = ["# KryptoDB Predicate Registry & Schema Rules\n"]
    for p in predicates:
        lines.append(f"[{p['name']}]")
        lines.append(f'cardinality = "{p["cardinality"]}"')
        lines.append(f'description = "{p["description"]}"')
        lines.append("")
    return {"predicates": predicates, "content": "\n".join(lines)}
