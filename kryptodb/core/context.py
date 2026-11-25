"""Request context and security claims for KryptoDB."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Set, FrozenSet, Union, Sequence


@dataclass(frozen=True)
class RequestContext:
    """Security context carrying tenant, actor, role, permissions, session, and active scopes.

    Every query, write, and retrieval in KryptoDB passes through this context to guarantee
    multitenancy and authorization boundaries across HTTP, WebSocket, MCP, and agent tools.
    """
    tenant_id: str
    agent_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    active_scopes: FrozenSet[str] = field(default_factory=frozenset)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_system: bool = False
    role: str = "writer"
    permissions: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not self.agent_id or not self.agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        if self.now.tzinfo is None:
            raise ValueError("now must be a timezone-aware datetime (UTC)")

    @classmethod
    def create(
        cls,
        tenant_id: str,
        agent_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        active_scopes: Optional[Union[Sequence[str], Set[str], FrozenSet[str]]] = None,
        now: Optional[datetime] = None,
        is_system: bool = False,
        role: Optional[str] = None,
        permissions: Optional[Union[Sequence[str], Set[str], FrozenSet[str]]] = None,
    ) -> RequestContext:
        """Factory enforcing validation and timezone awareness."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")

        dt_now = now if now is not None else datetime.now(timezone.utc)
        if dt_now.tzinfo is None:
            dt_now = dt_now.replace(tzinfo=timezone.utc)

        scopes: FrozenSet[str] = frozenset(active_scopes) if active_scopes is not None else frozenset()
        perms: FrozenSet[str] = frozenset(permissions) if permissions is not None else frozenset()
        resolved_role = (role or ("admin" if is_system else "writer")).strip().lower()

        return cls(
            tenant_id=tenant_id.strip(),
            agent_id=agent_id.strip(),
            user_id=user_id.strip() if user_id else None,
            session_id=session_id.strip() if session_id else None,
            active_scopes=scopes,
            now=dt_now,
            is_system=is_system,
            role=resolved_role,
            permissions=perms,
        )


def create_system_context(
    tenant_id: str = "default",
    now: Optional[datetime] = None,
) -> RequestContext:
    """Create a system context for migrations or background workers.

    Bypasses scope restrictions while maintaining strict tenant isolation.
    """
    return RequestContext.create(
        tenant_id=tenant_id,
        agent_id="system",
        now=now,
        is_system=True,
        role="admin",
        permissions={"*"},
    )


# Default system context for fallback internal operations
SYSTEM_CONTEXT = create_system_context()
