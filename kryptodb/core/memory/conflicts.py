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


class ConflictResolution(str, Enum):
    NO_CONFLICT = "NO_CONFLICT"
    DUPLICATE = "DUPLICATE"
    SUPERSEDE_OLD = "SUPERSEDE_OLD"
    FLAG_CONFLICT = "FLAG_CONFLICT"


class ConflictAction(str, Enum):
    CREATE_ACTIVE = "CREATE_ACTIVE"
    REFRESH_EXISTING = "REFRESH_EXISTING"
    SUPERSEDE_AND_CREATE = "SUPERSEDE_AND_CREATE"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    FLAG_CONFLICT = "FLAG_CONFLICT"


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
    """Evaluates proposed facts against active facts via the MemoryStore port."""

    def __init__(self, store: Optional[MemoryStore] = None, client: Any = None):
        self.store = store
        self._client = client

    def evaluate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float,
        context: RequestContext,
        store: Optional[MemoryStore] = None,
        is_explicit_mutation: bool = False,
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

        clean_obj = obj.strip().casefold()
        for fact in active_facts:
            if fact.object.strip().casefold() == clean_obj:
                return ConflictEvaluationResult(
                    action=ConflictAction.REFRESH_EXISTING,
                    existing_fact_id=fact.id,
                    version=fact.version,
                )

        if cardinality == Cardinality.MANY:
            return ConflictEvaluationResult(
                action=ConflictAction.CREATE_ACTIVE,
                version=1,
            )

        old_fact = active_facts[0]
        if not is_explicit_mutation and float(confidence) < float(old_fact.confidence):
            return ConflictEvaluationResult(
                action=ConflictAction.FLAG_CONFLICT,
                existing_fact_id=old_fact.id,
                version=old_fact.version,
            )
        return ConflictEvaluationResult(
            action=ConflictAction.SUPERSEDE_AND_CREATE,
            supersede_fact_id=old_fact.id,
            version=old_fact.version + 1,
        )

    def evaluate_proposal(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        tenant_id: str = "default",
        scope: Optional[str] = None,
        is_explicit_mutation: bool = False,
        store: Optional[MemoryStore] = None,
    ) -> Dict[str, Any]:
        """Evaluates proposed facts through MemoryStore (no raw Cypher in core/)."""
        norm_pred = predicate_registry.normalize(predicate)
        cardinality = predicate_registry.get_cardinality(norm_pred)
        st = store or self.store

        clean_scope = None if scope in ("AUTO", "*", "GLOBAL", "", None) else scope
        ctx = RequestContext.create(
            tenant_id=tenant_id,
            agent_id="conflict-engine",
            active_scopes=[clean_scope] if clean_scope else ["*"],
            is_system=clean_scope is None,
        )

        active_facts: List[MemoryRecord] = []
        if st is not None:
            try:
                active_facts = st.find_active_by_subject_predicate(subject.strip(), norm_pred, ctx)
            except Exception as exc:
                logger.warning("Error querying active facts for conflict check: %s", exc)
                active_facts = []

        if not active_facts:
            return {
                "resolution": ConflictResolution.NO_CONFLICT,
                "action": "CREATE_ACTIVE",
                "conflicting_facts": [],
            }

        records = [
            {
                "fact_id": f.id,
                "existing_object": f.object,
                "confidence": f.confidence,
                "version": f.version,
                "scope": f.scope,
            }
            for f in active_facts
        ]

        for r in records:
            if r["existing_object"].strip().casefold() == obj.strip().casefold():
                return {
                    "resolution": ConflictResolution.DUPLICATE,
                    "action": "REFRESH_EXISTING",
                    "existing_fact_id": r["fact_id"],
                    "conflicting_facts": [r],
                }

        if cardinality == Cardinality.MANY:
            return {
                "resolution": ConflictResolution.NO_CONFLICT,
                "action": "CREATE_ACTIVE",
                "conflicting_facts": [],
            }

        old_fact = records[0]
        old_conf = float(old_fact["confidence"] or 1.0)
        old_fact_id = old_fact["fact_id"]
        old_version = int(old_fact.get("version") or 1)

        if is_explicit_mutation or confidence >= old_conf:
            return {
                "resolution": ConflictResolution.SUPERSEDE_OLD,
                "action": "SUPERSEDE_AND_CREATE",
                "supersede_fact_id": old_fact_id,
                "next_version": old_version + 1,
                "conflicting_facts": records,
            }
        return {
            "resolution": ConflictResolution.FLAG_CONFLICT,
            "action": "STAGE_PROPOSED",
            "conflicting_facts": records,
            "reason": f"New confidence ({confidence}) does not exceed active fact confidence ({old_conf})",
        }


conflict_engine = ConflictEngine()
