from __future__ import annotations

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kryptodb.core.identity import require_auth
from kryptodb.core.retrieval import retrieval_service, AttributedMemoryResult
from kryptodb.core.memory import memory_service, MemoryRecord

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
    auth: Dict[str, Any] = Depends(require_auth)
):
    tenant_id = auth.get("tenant_id", "default")
    allowed_scopes = auth.get("scopes", ["default"])
    
    # Intersect requested scopes with authorized scopes if not admin
    if "*" not in allowed_scopes and req.scopes:
        effective_scopes = list(set(req.scopes) & set(allowed_scopes))
    else:
        effective_scopes = req.scopes or (None if "*" in allowed_scopes else allowed_scopes)

    results = retrieval_service.search(
        query=req.query,
        tenant_id=tenant_id,
        active_scopes=effective_scopes,
        top_k=req.top_k,
        max_hops=req.max_hops
    )
    return results

@router.post("/write", response_model=MemoryRecord)
async def write_fact(
    req: WriteFactRequest,
    auth: Dict[str, Any] = Depends(require_auth)
):
    tenant_id = auth.get("tenant_id", "default")
    agent_id = auth.get("agent_id", "agent-1")
    
    record = memory_service.write_fact(
        subject=req.subject,
        predicate=req.predicate,
        obj=req.object,
        description=req.description,
        scope=req.scope,
        tenant_id=tenant_id,
        confidence=req.confidence,
        source_agent_id=agent_id,
        dataset_id=req.dataset_id
    )
    return record

@router.delete("/revoke/{fact_id}")
async def revoke_fact(
    fact_id: str,
    auth: Dict[str, Any] = Depends(require_auth)
):
    tenant_id = auth.get("tenant_id", "default")
    success = memory_service.revoke_fact(fact_id=fact_id, tenant_id=tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Fact not found or already revoked")
    return {"status": "revoked", "fact_id": fact_id}

@router.get("/ontology")
async def get_ontology():
    from kryptodb.core.memory.predicates import predicate_registry
    predicates = predicate_registry.list_predicates()
    lines = ["# KryptoDB Predicate Registry & Schema Rules\n"]
    for p in predicates:
        lines.append(f"[{p['name']}]")
        lines.append(f"cardinality = \"{p['cardinality']}\"")
        lines.append(f"description = \"{p['description']}\"")
        lines.append("")
    return {"predicates": predicates, "content": "\n".join(lines)}

