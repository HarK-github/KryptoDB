from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field

class SessionContext(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "default"
    agent_id: str = "agent-1"
    active_scopes: Set[str] = Field(default_factory=lambda: {"default"})

class SessionManager:
    """Manages active agent session contexts."""
    def __init__(self):
        self._sessions: Dict[str, SessionContext] = {}

    def get_or_create(
        self,
        session_id: Optional[str] = None,
        tenant_id: str = "default",
        agent_id: str = "agent-1",
        scopes: Optional[List[str]] = None
    ) -> SessionContext:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]

        sid = session_id or str(uuid.uuid4())
        ctx = SessionContext(
            session_id=sid,
            tenant_id=tenant_id,
            agent_id=agent_id,
            active_scopes=set(scopes) if scopes else {"default"}
        )
        self._sessions[sid] = ctx
        return ctx

    def activate_scopes(self, session_id: str, scopes: List[str]) -> SessionContext:
        ctx = self.get_or_create(session_id)
        ctx.active_scopes.update(scopes)
        return ctx

    def deactivate_scopes(self, session_id: str, scopes: List[str]) -> SessionContext:
        ctx = self.get_or_create(session_id)
        for s in scopes:
            ctx.active_scopes.discard(s)
        return ctx

session_manager = SessionManager()
