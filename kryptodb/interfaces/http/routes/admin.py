from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from kryptodb.core.context import RequestContext
from kryptodb.core.identity.authentication import authenticator
from kryptodb.infrastructure.audit_ledger import audit_ledger
from kryptodb.interfaces.http.middleware.context import (
    authorize_or_403,
    require_admin_context,
    require_context,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


class CreateApiKeyRequest(BaseModel):
    agent_id: str
    tenant_id: str = "default"
    scopes: List[str] = ["default"]
    role: str = "writer"
    permissions: Optional[List[str]] = None
    expires_at: Optional[str] = None


@router.post("/api-keys")
async def create_api_key(
    req: CreateApiKeyRequest,
    context: RequestContext = Depends(require_admin_context),
):
    raw_key = f"kdb-{secrets.token_urlsafe(32)}"
    record = authenticator.register_agent_key(
        api_key=raw_key,
        agent_id=req.agent_id,
        tenant_id=req.tenant_id,
        scopes=req.scopes,
        role=req.role,
        permissions=req.permissions,
        expires_at=req.expires_at,
    )
    audit_ledger.append(
        actor=context.agent_id,
        action="create_api_key",
        tenant_id=req.tenant_id,
        scope=",".join(req.scopes),
        outcome="allowed",
        target_id=record["key_id"],
    )
    return {
        "api_key": raw_key,
        "key_id": record["key_id"],
        "agent_id": req.agent_id,
        "tenant_id": req.tenant_id,
        "role": record["role"],
        "scopes": record["scopes"],
        "permissions": record["permissions"],
        "warning": "Store this key now; it will not be shown again.",
    }


@router.get("/agents")
async def list_agents(context: RequestContext = Depends(require_context)):
    authorize_or_403(context, "memory:read", audit_ledger=audit_ledger)
    tenant_filter = None if context.is_system else context.tenant_id
    return authenticator.list_agents(tenant_id=tenant_filter)


@router.post("/agents")
async def update_agent_scopes(
    req: Dict[str, Any],
    context: RequestContext = Depends(require_admin_context),
):
    agent_id = req.get("agent_id")
    scopes = req.get("scopes", ["default"])
    tenant_id = req.get("tenant_id", "default")
    if not agent_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agent_id is required")
    authenticator.update_agent_scopes(agent_id, scopes, tenant_id=tenant_id)
    audit_ledger.append(
        actor=context.agent_id,
        action="update_agent_scopes",
        tenant_id=tenant_id,
        scope=",".join(scopes),
        outcome="allowed",
        target_id=agent_id,
    )
    return {"status": "ok", "agent_id": agent_id, "tenant_id": tenant_id, "scopes": scopes}


@router.get("/api-keys")
async def list_api_keys(context: RequestContext = Depends(require_admin_context)):
    return {"keys": authenticator.list_keys()}


@router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    context: RequestContext = Depends(require_admin_context),
):
    revoked = authenticator.revoke_key(key_id)
    audit_ledger.append(
        actor=context.agent_id,
        action="revoke_api_key",
        tenant_id="system",
        scope="*",
        outcome="allowed" if revoked else "not_found",
        target_id=key_id,
    )
    return {"status": "revoked" if revoked else "not_found", "key_id": key_id}


@router.get("/audit")
async def get_audit(context: RequestContext = Depends(require_context)):
    authorize_or_403(context, "memory:read", audit_ledger=audit_ledger)
    tenant_filter = None if context.is_system else context.tenant_id
    return audit_ledger.list_recent(tenant_id=tenant_filter, limit=100)
