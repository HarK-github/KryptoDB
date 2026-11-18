"""Unit tests for Phase 7: Conflict engine, predicate cardinality, quotation validation, and entity resolution."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.entity_resolution import EntityResolver
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.memory.write_policy import WritePolicyViolation
from kryptodb.core.ports import Event
from tests.fakes import FakeEmbedder, FakeEventStore, FakeMemoryStore


def test_predicate_cardinality_functional_supersedes():
    """LIVES_IN has cardinality ONE: proposing a new city must supersede the previous active fact."""
    store = FakeMemoryStore()
    service = MemoryService(store=store)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default"])

    # 1. Propose Alice LIVES_IN Seattle
    f1 = service.propose_memory("Alice", "LIVES_IN", "Seattle", ctx)
    assert f1.status == MemoryStatus.ACTIVE

    # 2. Propose Alice LIVES_IN Portland
    f2 = service.propose_memory("Alice", "LIVES_IN", "Portland", ctx)
    assert f2.status == MemoryStatus.ACTIVE
    assert f2.version == 2
    assert f2.previous_version_id == f1.id

    # Verify old fact is superseded
    assert store.facts[f1.id].status == MemoryStatus.SUPERSEDED
    # Active lookup returns ONLY Portland
    active = store.find_active_by_subject_predicate("Alice", "LIVES_IN", ctx)
    assert len(active) == 1
    assert active[0].object == "Portland"


def test_predicate_cardinality_relational_coexists():
    """WORKS_FOR has cardinality MANY: proposing multiple employers must allow coexistence."""
    store = FakeMemoryStore()
    service = MemoryService(store=store)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default"])

    f1 = service.propose_memory("Alice", "WORKS_FOR", "Acme Corp", ctx)
    f2 = service.propose_memory("Alice", "WORKS_FOR", "Beta Labs", ctx)

    assert f1.id != f2.id
    assert store.facts[f1.id].status == MemoryStatus.ACTIVE
    assert store.facts[f2.id].status == MemoryStatus.ACTIVE

    active = store.find_active_by_subject_predicate("Alice", "WORKS_FOR", ctx)
    assert len(active) == 2
    employers = {a.object for a in active}
    assert employers == {"Acme Corp", "Beta Labs"}


def test_exact_duplicate_does_not_create_new_edge():
    """Exact duplicate proposal returns existing fact without creating redundant edges."""
    store = FakeMemoryStore()
    service = MemoryService(store=store)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default"])

    f1 = service.propose_memory("Alice", "WORKS_FOR", "Acme Corp", ctx)
    f2 = service.propose_memory("Alice", "WORKS_FOR", "Acme Corp", ctx)

    assert f1.id == f2.id
    assert len(store.facts) == 1


def test_quote_substring_validation():
    """If quote_substring is provided, it must be an exact substring of the source event."""
    store = FakeMemoryStore()
    event_store = FakeEventStore()
    service = MemoryService(store=store, event_store=event_store)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", session_id="sess-1")

    # Record event in session
    event_store.append(
        Event(
            id="ev-100",
            session_id="sess-1",
            event_type="user",
            content="I was born in London and I love pizza.",
            tenant_id="tenant-acme",
        ),
        ctx,
    )

    # 1. Valid quote substring: should succeed
    f_valid = service.propose_memory(
        subject="User",
        predicate="BORN_IN",
        obj="London",
        context=ctx,
        source_event_id="ev-100",
        quote_substring="born in London",
        session_id="sess-1",
    )
    assert f_valid is not None

    # 2. Fabricated quote substring: must raise WritePolicyViolation
    with pytest.raises(WritePolicyViolation, match="quote not found in source message"):
        service.propose_memory(
            subject="User",
            predicate="LIVES_IN",
            obj="Paris",
            context=ctx,
            source_event_id="ev-100",
            quote_substring="I currently live in Paris",
            session_id="sess-1",
        )


def test_entity_resolution_no_silent_auto_merge():
    """Fuzzy entity matches create POSSIBLE_SAME_AS candidate but NEVER auto-merge."""
    embedder = FakeEmbedder(dimension=64)
    store = FakeMemoryStore(embedder=embedder)
    resolver = EntityResolver(fuzzy_threshold=0.92)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default"])

    # Create Alice Smith
    store.write_fact(
        MemoryRecord(
            id="f-alice",
            subject="Alice Smith",
            predicate="WORKS_FOR",
            object="Acme",
            tenant_id="tenant-acme",
        ),
        ctx,
    )

    # 1. Exact normalized match: auto-merges
    exact_res = resolver.resolve("  alice smith  ", ctx, store, embedder)
    assert exact_res.is_exact_merge is True
    assert exact_res.matched_entity is not None
    assert exact_res.matched_entity.name == "Alice Smith"

    # 2. Alice Johnson (different person): MUST NOT auto-merge
    diff_res = resolver.resolve("Alice Johnson", ctx, store, embedder)
    assert diff_res.is_exact_merge is False
    assert diff_res.matched_entity is None
