"""Contradiction detection and conflict engine operating on MemoryStore."""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.memory.predicates import Cardinality, predicate_registry

if TYPE_CHECKING:
    from kryptodb.core.ports import MemoryStore

logger = logging.getLogger("kryptodb.core.memory.conflicts")


class ConflictAction(str, Enum):
    CREATE_ACTIVE = "CREATE_ACTIVE"
    REFRESH_EXISTING = "REFRESH_EXISTING"
    SUPERSEDE_AND_CREATE = "SUPERSEDE_AND_CREATE"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"


class ConflictEvaluationResult:
    def __init__(
        self,
        action: ConflictAction,
        existing_fact_id: Optional[str] = None,
        supersede_fact_id: Optional[str] = None,
        version: int = 1,
    ):
        self.action = action
        self.existing_fact_id = existing_fact_id
        self.supersede_fact_id = supersede_fact_id
        self.version = version


class ConflictEngine:
    """Evaluates proposed facts against active facts in MemoryStore."""

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store

    def evaluate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float,
        context: RequestContext,
        store: Optional[MemoryStore] = None,
    ) -> ConflictEvaluationResult:
        """Determines whether a proposed fact creates, refreshes, or supersedes an existing fact."""
        st = store or self.store
        if not st:
            return ConflictEvaluationResult(action=ConflictAction.CREATE_ACTIVE)

        norm_pred = predicate_registry.normalize(predicate)
        cardinality = predicate_registry.get_cardinality(norm_pred)

        active_facts = st.find_active_by_subject_predicate(subject, norm_pred, context)
        if not active_facts:
            return ConflictEvaluationResult(action=ConflictAction.CREATE_ACTIVE)

        # 1. Exact duplicate check
        clean_obj = obj.strip().casefold()
        for fact in active_facts:
            if fact.object.strip().casefold() == clean_obj:
                return ConflictEvaluationResult(
                    action=ConflictAction.REFRESH_EXISTING,
                    existing_fact_id=fact.id,
                    version=fact.version,
                )

        # 2. Relational (MANY): coexists peacefully
        if cardinality == Cardinality.MANY:
            return ConflictEvaluationResult(
                action=ConflictAction.CREATE_ACTIVE,
                version=1,
            )

        # 3. Functional (ONE): conflict! Old fact must be superseded
        old_fact = active_facts[0]
        return ConflictEvaluationResult(
            action=ConflictAction.SUPERSEDE_AND_CREATE,
            supersede_fact_id=old_fact.id,
            version=old_fact.version + 1,
        )
