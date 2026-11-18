"""HTTP context resolution middleware extracting RequestContext from request headers."""

from __future__ import annotations

import os
from typing import Optional
from fastapi import Header, Request

from kryptodb.core.context import RequestContext
from kryptodb.core.identity.authentication import authenticator


def get_request_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> RequestContext:
    """Dependency injecting validated RequestContext into HTTP route handlers."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    claims = authenticator.verify_token(token) if token else None

    tenant_id = x_tenant_id or (claims.get("tenant_id") if claims else "default") or "default"
    agent_id = (claims.get("agent_id") if claims else "agent-1") or "agent-1"
    raw_scopes = (claims.get("scopes") if claims else ["default"]) or ["default"]
    is_admin = bool(claims.get("is_admin", False)) if claims else False

    # In development mode, default to full access if unauthenticated
    if not claims and os.getenv("KRYPTODB_ENV", "development") == "development":
        raw_scopes = ["default", "research", "engineering"]

    return RequestContext.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=x_user_id,
        session_id=x_session_id,
        active_scopes=raw_scopes,
        is_system=is_admin or ("*" in raw_scopes),
    )
