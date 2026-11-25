from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from kryptodb.core.memory.models import MemoryStatus, MemoryRecord
from kryptodb.core.memory.predicates import PredicateRegistry, Cardinality
from kryptodb.core.memory.conflicts import ConflictEngine, ConflictResolution
from kryptodb.core.memory.memory_service import MemoryService

def test_predicate_registry():
    reg = PredicateRegistry()
    assert reg.normalize("lives in") == "LIVES_IN"
    assert reg.get_cardinality("LIVES_IN") == Cardinality.ONE
    assert reg.get_cardinality("lives-in") == Cardinality.ONE
    assert reg.get_cardinality("WORKS_FOR") == Cardinality.ONE
    assert reg.get_cardinality("MEMBER_OF") == Cardinality.MANY
    assert reg.get_cardinality("UNKNOWN_CUSTOM_PREDICATE") == Cardinality.MANY
    
    # Custom registration
    reg.register("OWNS_PET", Cardinality.ONE)
    assert reg.get_cardinality("OWNS_PET") == Cardinality.ONE

def test_conflict_engine_cardinality_one_supersede():
    mock_store = MagicMock()
    # Mock an existing active fact: Alice LIVES_IN New York (conf=0.8)
    mock_store.find_active_by_subject_predicate.return_value = [
        MemoryRecord(
            id="old-fact-1",
            subject="Alice",
            predicate="LIVES_IN",
            object="New York",
            confidence=0.8,
            version=1,
            status=MemoryStatus.ACTIVE,
            tenant_id="tenant-1",
            scope="default",
        )
    ]

    engine = ConflictEngine(store=mock_store)
    
    # Alice now proposed to live in San Francisco with higher confidence 0.95
    res = engine.evaluate_proposal(
        subject="Alice",
        predicate="LIVES_IN",
        obj="San Francisco",
        confidence=0.95,
        tenant_id="tenant-1"
    )

    assert res["resolution"] == ConflictResolution.SUPERSEDE_OLD
    assert res["action"] == "SUPERSEDE_AND_CREATE"
    assert res["supersede_fact_id"] == "old-fact-1"
    assert res["next_version"] == 2

def test_conflict_engine_cardinality_one_flag_conflict():
    mock_store = MagicMock()
    # Existing active fact: Alice LIVES_IN New York (conf=0.9)
    mock_store.find_active_by_subject_predicate.return_value = [
        MemoryRecord(
            id="old-fact-1",
            subject="Alice",
            predicate="LIVES_IN",
            object="New York",
            confidence=0.9,
            version=1,
            status=MemoryStatus.ACTIVE,
            tenant_id="tenant-1",
            scope="default",
        )
    ]

    engine = ConflictEngine(store=mock_store)
    
    # Lower confidence 0.7 proposal
    res = engine.evaluate_proposal(
        subject="Alice",
        predicate="LIVES_IN",
        obj="San Francisco",
        confidence=0.7,
        tenant_id="tenant-1"
    )

    assert res["resolution"] == ConflictResolution.FLAG_CONFLICT
    assert res["action"] == "STAGE_PROPOSED"

def test_conflict_engine_cardinality_many_coexist():
    mock_store = MagicMock()
    # Alice already WORKS_FOR Acme Corp
    mock_store.find_active_by_subject_predicate.return_value = [
        MemoryRecord(
            id="old-fact-1",
            subject="Alice",
            predicate="MEMBER_OF",
            object="Acme Corp",
            confidence=0.9,
            version=1,
            status=MemoryStatus.ACTIVE,
            tenant_id="tenant-1",
            scope="default",
        )
    ]

    engine = ConflictEngine(store=mock_store)
    
    # Alice also MEMBER_OF Beta Industries (cardinality MANY)
    res = engine.evaluate_proposal(
        subject="Alice",
        predicate="MEMBER_OF",
        obj="Beta Industries",
        confidence=0.85,
        tenant_id="tenant-1"
    )

    assert res["resolution"] == ConflictResolution.NO_CONFLICT
    assert res["action"] == "CREATE_ACTIVE"

def test_memory_service_write_and_revoke():
    mock_store = MagicMock()
    def mock_write(fact, ctx):
        return fact
    mock_store.write_fact.side_effect = mock_write

    service = MemoryService(store=mock_store)
    record = service.write_fact(
        subject="Alice",
        predicate="CONTRIBUTED_TO",
        obj="LangChain",
        confidence=0.95,
        tenant_id="tenant-1",
        bypass_conflict_check=True
    )

    assert record.subject == "Alice"
    assert record.predicate == "CONTRIBUTED_TO"
    assert record.object == "LangChain"
    assert record.status == MemoryStatus.ACTIVE
    assert record.tenant_id == "tenant-1"

    # Revoke
    revoked = service.revoke_fact("fact-101", tenant_id="tenant-1")
    assert revoked is True
    mock_store.revoke.assert_called_once()
