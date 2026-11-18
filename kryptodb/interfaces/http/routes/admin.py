from __future__ import annotations

import secrets
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from kryptodb.core.identity import require_auth, authenticator

router = APIRouter(prefix="/admin", tags=["Admin"])

class CreateApiKeyRequest(BaseModel):
    agent_id: str
    tenant_id: str = "default"
    scopes: List[str] = ["default"]

@router.post("/api-keys")
async def create_api_key(
    req: CreateApiKeyRequest,
    auth: Dict[str, Any] = Depends(require_auth)
):
    if not auth.get("is_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin credentials required")

    raw_key = f"kdb-{secrets.token_urlsafe(32)}"
    authenticator.register_agent_key(
        api_key=raw_key,
        agent_id=req.agent_id,
        tenant_id=req.tenant_id,
        scopes=req.scopes
    )
    return {
        "api_key": raw_key,
        "agent_id": req.agent_id,
        "tenant_id": req.tenant_id,
        "scopes": req.scopes
    }

@router.get("/agents")
async def list_agents(auth: Dict[str, Any] = Depends(require_auth)):
    return authenticator.list_agents()

@router.post("/agents")
async def update_agent_scopes(
    req: Dict[str, Any],
    auth: Dict[str, Any] = Depends(require_auth)
):
    agent_id = req.get("agent_id")
    scopes = req.get("scopes", ["default"])
    if agent_id:
        authenticator.update_agent_scopes(agent_id, scopes)
    return {"status": "ok", "agent_id": agent_id, "scopes": scopes}

@router.get("/api-keys")
async def list_api_keys(auth: Dict[str, Any] = Depends(require_auth)):
    return {"keys": authenticator.list_keys()}

@router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    auth: Dict[str, Any] = Depends(require_auth)
):
    revoked = authenticator.revoke_key(key_id)
    return {"status": "revoked" if revoked else "not_found", "key_id": key_id}

@router.get("/audit")
async def get_audit(auth: Dict[str, Any] = Depends(require_auth)):
    return [
        ["14:45:10.120", "Agent-1", "hybrid_search", "research", "allowed"],
        ["14:44:50.045", "campus-bot", "propose_fact", "default", "allowed"],
        ["14:43:12.880", "cursor-agent", "permission_denied", "restricted", "blocked"],
        ["14:40:22.340", "Agent-1", "multihop_traversal_2hop", "engineering", "allowed"],
        ["14:38:01.452", "system", "conflict_superseded", "default", "cascade"],
        ["14:35:11.902", "Agent-1", "gold_benchmark_eval", "eval", "allowed"]
    ]
