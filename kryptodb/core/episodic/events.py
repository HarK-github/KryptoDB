"""Episodic memory event recording."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.ports import Event, EventStore


def record_event(
    session_id: str,
    role: str,
    content: str,
    context: RequestContext,
    store: EventStore,
    scope: str = "default",
    event_id: Optional[str] = None,
) -> Event:
    """Records an episodic conversation turn into the event store."""
    ev = Event(
        id=event_id or f"ev-{uuid.uuid4().hex[:12]}",
        session_id=session_id.strip(),
        event_type=role.strip(),
        content=content.strip(),
        tenant_id=context.tenant_id,
        scope=scope,
        agent_id=context.agent_id,
        user_id=context.user_id,
        timestamp=context.now,
    )
    store.append(ev, context)
    return ev
