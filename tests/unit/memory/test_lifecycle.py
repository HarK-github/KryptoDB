"""Unit tests for Phase 7: Memory lifecycle state machine, validation, and user controls."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.lifecycle import (
    InvalidStateTransitionError,
    can_transition,
    transition,
)
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.core.ports import Event
from tests.fakes import FakeEventStore, FakeMemoryStore


def test_lifecycle_state_machine_transitions():
    # Valid transitions
    assert transition(MemoryStatus.PROPOSED, MemoryStatus.ACTIVE) == MemoryStatus.ACTIVE
    assert transition(MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED) == MemoryStatus.SUPERSEDED
    assert transition(MemoryStatus.ACTIVE, MemoryStatus.REVOKED) == MemoryStatus.REVOKED

    # Invalid transitions
    with pytest.raises(InvalidStateTransitionError):
        transition(MemoryStatus.REVOKED, MemoryStatus.ACTIVE)

    with pytest.raises(InvalidStateTransitionError):
        transition(MemoryStatus.SUPERSEDED, MemoryStatus.ACTIVE)

    with pytest.raises(InvalidStateTransitionError):
        transition(MemoryStatus.SUPERSEDED, MemoryStatus.PROPOSED)


def test_user_memory_controls_correct_and_forget():
    store = FakeMemoryStore()
    service = MemoryService(store=store)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default"])

    # 1. Propose fact
    fact = service.propose_memory(
        subject="Alice",
        predicate="LIVES_IN",
        obj="London",
        context=ctx,
    )
    assert fact.object == "London"
    assert fact.version == 1

    # 2. Correct fact: London -> Paris
    corrected = service.correct_memory(fact.id, "Paris", ctx)
    assert corrected.object == "Paris"
    assert corrected.version == 2
    assert corrected.previous_version_id == fact.id

    # Old fact should be superseded
    old = store.facts[fact.id]
    assert old.status == MemoryStatus.SUPERSEDED

    # 3. Forget memory (revoke)
    service.forget_memory(corrected.id, ctx)
    revoked = store.facts[corrected.id]
    assert revoked.status == MemoryStatus.REVOKED

    # 4. Forget user
    count = service.forget_user(user_id="Alice", context=ctx)
    assert count >= 1
