"""Verify core domain logic and protocols against in-memory fake test doubles."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.ports import Event, EpisodeSummary
from tests.fakes import FakeMemoryStore, FakeEventStore, FakeEmbedder, FakeReranker


def test_fake_memory_store_lifecycle_and_multihop():
    embedder = FakeEmbedder(dimension=64)
    store = FakeMemoryStore(embedder=embedder)
    ctx = RequestContext.create(
        tenant_id="tenant-alpha",
        agent_id="agent-1",
        active_scopes=["default", "research"],
    )

    # 1. Write fact 1: Alice WORKS_AT Acme
    f1 = MemoryRecord(
        id="fact-1",
        subject="Alice",
        predicate="WORKS_AT",
        object="Acme Corp",
        tenant_id="tenant-alpha",
        scope="default",
        status=MemoryStatus.ACTIVE,
        confidence=1.0,
    )
    store.write_fact(f1, ctx)

    # 2. Write fact 2: Acme Corp LOCATED_IN New York
    f2 = MemoryRecord(
        id="fact-2",
        subject="Acme Corp",
        predicate="LOCATED_IN",
        object="New York",
        tenant_id="tenant-alpha",
        scope="research",
        status=MemoryStatus.ACTIVE,
        confidence=0.9,
    )
    store.write_fact(f2, ctx)

    # 3. Retrieve fact
    retrieved = store.get_fact("fact-1", ctx)
    assert retrieved is not None
    assert retrieved.subject == "Alice"
    assert retrieved.object == "Acme Corp"

    # 4. Multi-hop traversal (Alice -> Acme Corp -> New York)
    paths = store.traverse_2hop(["Alice"], ctx)
    assert len(paths) >= 2  # 1-hop path and 2-hop path
    two_hop = [p for p in paths if p.hops == 2]
    assert len(two_hop) == 1
    assert two_hop[0].seed == "Alice"
    assert two_hop[0].target == "New York"
    assert two_hop[0].score == pytest.approx(0.9)

    # 5. Supersession
    f3 = MemoryRecord(
        id="fact-3",
        subject="Alice",
        predicate="WORKS_AT",
        object="Beta Labs",
        tenant_id="tenant-alpha",
        scope="default",
        status=MemoryStatus.ACTIVE,
        confidence=1.0,
    )
    store.write_fact(f3, ctx)
    store.supersede("fact-1", "fact-3", ctx)

    # Old fact is no longer active in find_active_by_subject_predicate
    active = store.find_active_by_subject_predicate("Alice", "WORKS_AT", ctx)
    assert len(active) == 1
    assert active[0].id == "fact-3"


def test_fake_event_store_and_summaries():
    event_store = FakeEventStore()
    ctx = RequestContext.create(tenant_id="tenant-alpha", agent_id="agent-1")

    # Append events
    e1 = Event(
        id="ev-1",
        session_id="sess-100",
        event_type="user_message",
        content="I prefer vegetarian meals.",
        tenant_id="tenant-alpha",
    )
    e2 = Event(
        id="ev-2",
        session_id="sess-100",
        event_type="agent_message",
        content="Understood, vegetarian saved.",
        tenant_id="tenant-alpha",
    )
    event_store.append(e1, ctx)
    event_store.append(e2, ctx)

    history = event_store.list_by_session("sess-100", ctx)
    assert len(history) == 2
    assert history[0].content == "I prefer vegetarian meals."

    # Store summary
    summary = EpisodeSummary(
        id="ep-1",
        session_id="sess-100",
        summary="User discussed dietary preferences.",
        topics=["diet", "preferences"],
        tenant_id="tenant-alpha",
    )
    summary_id = event_store.store_summary(summary, ctx)
    assert summary_id == "ep-1"
