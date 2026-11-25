"""Unified HTTP context & authorization middleware across all REST and WebSocket routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from fastapi import Depends, Header, HTTPException, Request, status

from kryptodb.core.context import RequestContext
from kryptodb.core.identity.authentication import authenticator
from kryptodb.core.identity.policy import PolicyDeniedError, policy_engine


def build_context_from_claims(
    claims: Dict[str, Any],
    *,
    x_tenant_id: Optional[str] = None,
    x_session_id: Optional[str] = None,
    x_user_id: Optional[str] = None,
) -> RequestContext:
    """Converts verified Authenticator claims into a RequestContext."""
    is_admin = bool(claims.get("is_admin", False))
    if is_admin and x_tenant_id:
        tenant_id = x_tenant_id.strip()
    else:
        tenant_id = str(claims.get("tenant_id") or "default")

    agent_id = str(claims.get("agent_id") or "agent-1")
    raw_scopes = list(claims.get("scopes") or [])
    role = str(claims.get("role") or ("admin" if is_admin else "writer"))
    permissions = list(claims.get("permissions") or [])

    return RequestContext.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=x_user_id,
        session_id=x_session_id,
        active_scopes=raw_scopes,
        is_system=is_admin or ("*" in raw_scopes),
        role=role,
        permissions=permissions,
    )


def require_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> RequestContext:
    """Fail-closed dependency injecting validated RequestContext into all HTTP route handlers."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer authentication token",
        )

    claims = authenticator.verify_token(token)
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return build_context_from_claims(
        claims,
        x_tenant_id=x_tenant_id,
        x_session_id=x_session_id,
        x_user_id=x_user_id,
    )


# Backward-compatible alias for /v1/* routes
get_request_context = require_context


def require_admin_context(
    context: RequestContext = Depends(require_context),
) -> RequestContext:
    """Requires admin role/permissions via PolicyEngine."""
    authorize_or_403(context, "admin:*")
    return context


def authorize_or_403(
    context: RequestContext,
    action: str,
    *,
    target_tenant: Optional[str] = None,
    target_scopes: Optional[Sequence[str]] = None,
    target_agent_id: Optional[str] = None,
    audit_ledger: Any = None,
) -> Optional[List[str]]:
    """Runs PolicyEngine.authorize and converts PolicyDeniedError into HTTP 403."""
    try:
        return policy_engine.authorize(
            context,
            action,
            target_tenant=target_tenant,
            target_scopes=target_scopes,
            target_agent_id=target_agent_id,
            audit_ledger=audit_ledger,
        )
    except PolicyDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
