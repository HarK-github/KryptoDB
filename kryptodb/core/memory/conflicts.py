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
    """Evaluates proposed facts against active facts in MemoryStore or Neo4j."""

    def __init__(self, store: Optional[MemoryStore] = None, client: Any = None):
        self.store = store
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from kryptodb.infrastructure.neo4j.client import neo4j_client
                self._client = neo4j_client
            except Exception:
                self._client = None
        return self._client

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

    def evaluate_proposal(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        tenant_id: str = "default",
        scope: Optional[str] = None,
        is_explicit_mutation: bool = False,
    ) -> Dict[str, Any]:
        """Queries active facts for (subject, predicate) within tenant_id and scope,
        and determines conflict resolution outcome.
        """
        norm_pred = predicate_registry.normalize(predicate)
        cardinality = predicate_registry.get_cardinality(norm_pred)

        query = """
        MATCH (s:Entity {name: $subject, tenant_id: $tenant_id})-[r:FACT {predicate: $predicate, status: 'active'}]->(o:Entity)
        WHERE r.tenant_id = $tenant_id AND ($scope IS NULL OR r.permission_scope = $scope)
        RETURN r.id AS fact_id, o.name AS existing_object, r.confidence AS confidence, r.version AS version
        ORDER BY r.version DESC, r.created_at DESC
        """
        clean_scope = None if scope in ("AUTO", "*", "GLOBAL", "") else scope
        params = {
            "subject": subject.strip(),
            "predicate": norm_pred,
            "tenant_id": tenant_id,
            "scope": clean_scope,
        }

        records = []
        if self.client is not None:
            try:
                records, _, _ = self.client.execute_query_with_retry(query, **params)
            except Exception as exc:
                logger.warning("Error querying existing facts for conflict check: %s", exc)
                records = []

        if not records:
            return {
                "resolution": ConflictResolution.NO_CONFLICT,
                "action": "CREATE_ACTIVE",
                "conflicting_facts": [],
            }

        # Check for exact duplicate
        for r in records:
            if r["existing_object"].strip().casefold() == obj.strip().casefold():
                return {
                    "resolution": ConflictResolution.DUPLICATE,
                    "action": "REFRESH_EXISTING",
                    "existing_fact_id": r["fact_id"],
                    "conflicting_facts": [r],
                }

        # If predicate is 1-to-many, multiple distinct objects can coexist happily
        if cardinality == Cardinality.MANY:
            return {
                "resolution": ConflictResolution.NO_CONFLICT,
                "action": "CREATE_ACTIVE",
                "conflicting_facts": [],
            }

        # Predicate is Cardinality.ONE (Functional) and objects differ: Conflict!
        old_fact = records[0]
        old_conf = float(old_fact["confidence"] or 1.0)
        old_fact_id = old_fact["fact_id"]
        old_version = int(old_fact.get("version") or 1)

        # Explicit mutation or equal/higher confidence allows supersession (recency wins on tie)
        if is_explicit_mutation or confidence >= old_conf:
            return {
                "resolution": ConflictResolution.SUPERSEDE_OLD,
                "action": "SUPERSEDE_AND_CREATE",
                "supersede_fact_id": old_fact_id,
                "next_version": old_version + 1,
                "conflicting_facts": records,
            }
        else:
            return {
                "resolution": ConflictResolution.FLAG_CONFLICT,
                "action": "STAGE_PROPOSED",
                "conflicting_facts": records,
                "reason": f"New confidence ({confidence}) does not exceed active fact confidence ({old_conf})",
            }


conflict_engine = ConflictEngine()

