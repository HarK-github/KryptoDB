"""Unified PolicyEngine and JSON policy configuration loader for KryptoDB."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, FrozenSet, List, Optional, Sequence
from pydantic import BaseModel, Field

from kryptodb.core.context import RequestContext


class PolicyDeniedError(PermissionError):
    """Raised when a RequestContext is not authorized for an action, tenant, or scope."""


ROLE_PERMISSIONS: dict[str, FrozenSet[str]] = {
    "reader": frozenset({"memory:read", "graph:read", "chat:read"}),
    "writer": frozenset({
        "memory:read",
        "memory:write",
        "memory:revoke",
        "graph:read",
        "chat:read",
        "chat:write",
    }),
    "admin": frozenset({"*"}),
}


class AgentPolicyEntry(BaseModel):
    agent_id: str = Field(..., min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    role: str = Field(default="writer")
    scopes: List[str] = Field(default_factory=lambda: ["default"])
    permissions: Optional[List[str]] = None
    api_key_env: Optional[str] = None


class PolicyConfig(BaseModel):
    agents: List[AgentPolicyEntry] = Field(default_factory=list)


def load_policy_config(path: str | Path) -> PolicyConfig:
    """Loads and validates a JSON policy config file via Pydantic."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return PolicyConfig.model_validate(raw) if hasattr(PolicyConfig, "model_validate") else PolicyConfig.parse_obj(raw)


class PolicyEngine:
    """Centralized fail-closed authorization engine across HTTP, WebSocket, MCP, and tools."""

    @staticmethod
    def _effective_permissions(ctx: RequestContext) -> FrozenSet[str]:
        if ctx.is_system or ctx.role == "admin":
            return frozenset({"*"})
        if ctx.permissions:
            return ctx.permissions
        return ROLE_PERMISSIONS.get(ctx.role, frozenset())

    @classmethod
    def _action_allowed(cls, ctx: RequestContext, action: str) -> bool:
        perms = cls._effective_permissions(ctx)
        if "*" in perms or action in perms:
            return True
        prefix = action.split(":", 1)[0]
        return f"{prefix}:*" in perms

    def authorize(
        self,
        ctx: RequestContext,
        action: str,
        *,
        target_tenant: Optional[str] = None,
        target_scopes: Optional[Sequence[str]] = None,
        target_agent_id: Optional[str] = None,
        audit_ledger: Any = None,
    ) -> Optional[List[str]]:
        """Enforces tenant, role/action, agent-ownership, and scope boundaries.

        Returns the effective list of authorized scopes (or None when admin requests all scopes).
        Raises PolicyDeniedError (fail-closed) on any violation.
        """
        def _deny(reason: str) -> None:
            if audit_ledger is not None:
                try:
                    audit_ledger.append(
                        actor=ctx.agent_id,
                        action=action,
                        tenant_id=target_tenant or ctx.tenant_id,
                        scope=",".join(target_scopes or list(ctx.active_scopes)),
                        outcome="denied",
                        metadata={"reason": reason},
                    )
                except Exception:
                    pass
            raise PolicyDeniedError(reason)

        # 1. Action / Role permission check
        if not self._action_allowed(ctx, action):
            _deny(f"Action '{action}' is not permitted for role '{ctx.role}'")

        # 2. Tenant isolation check
        if target_tenant and not ctx.is_system and target_tenant != ctx.tenant_id:
            _deny(f"Cross-tenant access to '{target_tenant}' is forbidden")
        if target_tenant == "system" and not ctx.is_system:
            _deny("System tenant access requires admin credentials")

        # 3. Agent ownership check (for agent-scoped routes & chat threads)
        if target_agent_id and not ctx.is_system and target_agent_id != ctx.agent_id:
            _deny(f"Agent identity '{ctx.agent_id}' cannot act as '{target_agent_id}'")

        # 4. Scope intersection (fail-closed)
        has_wildcard = ctx.is_system or ("*" in ctx.active_scopes)
        if target_scopes:
            requested = [s.strip() for s in target_scopes if s and s.strip()]
            if not requested:
                return None if has_wildcard else sorted(ctx.active_scopes)
            if has_wildcard:
                return sorted(set(requested))
            disallowed = [s for s in requested if s not in ctx.active_scopes]
            if disallowed:
                _deny(f"Requested scope(s) {disallowed} are outside API-key authorization")
            return sorted(set(requested))

        if has_wildcard:
            return None
        if not ctx.active_scopes:
            _deny("Principal has no authorized scopes")
        return sorted(ctx.active_scopes)


policy_engine = PolicyEngine()
