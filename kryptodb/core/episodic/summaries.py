"""Episodic session summarization and episode linking."""

from __future__ import annotations

import uuid
from typing import Any, Callable, List, Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.ports import EpisodeSummary, EventStore


def default_extractive_summarizer(events: List[Any]) -> tuple[str, List[str]]:
    """Heuristic summary and key topic extraction when an external LLM is not passed."""
    if not events:
        return "Empty session.", []

    lines = [f"{e.event_type}: {e.content}" for e in events[:10]]
    summary = "Conversation overview:\n" + "\n".join(lines)

    # Extract capitalized words as candidate topic entities
    topics = set()
    for e in events:
        for word in e.content.split():
            clean = word.strip(".,!?:;\"'()[]{}")
            if clean and clean[0].isupper() and len(clean) > 2:
                topics.add(clean)

    return summary, sorted(list(topics))[:8]


def summarize_session(
    session_id: str,
    context: RequestContext,
    store: EventStore,
    summarizer: Optional[Callable[[List[Any]], tuple[str, List[str]]]] = None,
    scope: str = "default",
    episode_id: Optional[str] = None,
) -> EpisodeSummary:
    """Summarizes a completed session and registers the EpisodeSummary in durable storage."""
    events = store.list_by_session(session_id, context)
    fn = summarizer or default_extractive_summarizer
    summary_text, topics = fn(events)

    summary = EpisodeSummary(
        id=episode_id or f"ep-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        summary=summary_text,
        topics=topics,
        tenant_id=context.tenant_id,
        scope=scope,
        timestamp=context.now,
    )
    store.store_summary(summary, context)
    return summary
