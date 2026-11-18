from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.core.memory.predicates import predicate_registry, Cardinality

logger = logging.getLogger("kryptodb.conflict_engine")

class ConflictResolution(str, Enum):
    NO_CONFLICT = "NO_CONFLICT"
    DUPLICATE = "DUPLICATE"
    SUPERSEDE_OLD = "SUPERSEDE_OLD"
    FLAG_CONFLICT = "FLAG_CONFLICT"

class ConflictEngine:
    """
    Evaluates new fact proposals against active Neo4j facts using
    predicate cardinality rules and confidence scoring.
    """
    def __init__(self, client: Any = None):
        self.client = client or neo4j_client

    def evaluate_proposal(
        self,
        subject: str,
        predicate: str,
        obj: str,
        confidence: float = 1.0,
        tenant_id: str = "default",
        scope: Optional[str] = None,
        is_explicit_mutation: bool = False
    ) -> Dict[str, Any]:
        """
        Queries active facts for (subject, predicate) within tenant_id and scope,
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
            "scope": clean_scope
        }

        try:
            records, _, _ = self.client.execute_query_with_retry(query, **params)
        except Exception as exc:
            logger.warning("Error querying existing facts for conflict check: %s", exc)
            records = []

        if not records:
            return {
                "resolution": ConflictResolution.NO_CONFLICT,
                "action": "CREATE_ACTIVE",
                "conflicting_facts": []
            }

        # Check for exact duplicate
        for r in records:
            if r["existing_object"].strip().casefold() == obj.strip().casefold():
                return {
                    "resolution": ConflictResolution.DUPLICATE,
                    "action": "REFRESH_EXISTING",
                    "existing_fact_id": r["fact_id"],
                    "conflicting_facts": [r]
                }

        # If predicate is 1-to-many, multiple distinct objects can coexist happily
        if cardinality == Cardinality.MANY:
            return {
                "resolution": ConflictResolution.NO_CONFLICT,
                "action": "CREATE_ACTIVE",
                "conflicting_facts": []
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
                "conflicting_facts": records
            }
        else:
            return {
                "resolution": ConflictResolution.FLAG_CONFLICT,
                "action": "STAGE_PROPOSED",
                "conflicting_facts": records,
                "reason": f"New confidence ({confidence}) does not exceed active fact confidence ({old_conf})"
            }

conflict_engine = ConflictEngine()
