"""Predicate registry for relationship typing, cardinality resolution, canonical normalization, and inverses."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Tuple


class Cardinality(str, Enum):
    ONE = "ONE"    # Functional (1-to-1): e.g. LIVES_IN, PRIMARY_EMAIL (supersedes previous value)
    MANY = "MANY"  # Relational (1-to-N): e.g. WORKS_FOR, HAS_SKILL, INTERACTION (coexists)


class PredicateRegistry:
    """Authoritative registry for predicate schemas, cardinality, and normalization."""

    def __init__(self):
        # Cardinalities
        self._cardinality: Dict[str, Cardinality] = {
            # Functional (ONE)
            "LIVES_IN": Cardinality.ONE,
            "LOCATED_IN": Cardinality.ONE,
            "PRIMARY_EMAIL": Cardinality.ONE,
            "PREFERRED_NAME": Cardinality.ONE,
            "DATE_OF_BIRTH": Cardinality.ONE,
            "HEADQUARTERED_IN": Cardinality.ONE,
            "BORN_IN": Cardinality.ONE,
            "REPORTS_TO": Cardinality.ONE,
            "IS_CEO_OF": Cardinality.ONE,
            "CAPITAL_OF": Cardinality.ONE,
            "HAS_EMAIL": Cardinality.ONE,
            "HAS_ROLE": Cardinality.ONE,
            "HAS_NAME": Cardinality.ONE,
            "FAVORITE_COLOR": Cardinality.ONE,

            # Relational (MANY)
            "IS": Cardinality.MANY,
            "IS_A": Cardinality.MANY,
            "HAS": Cardinality.MANY,
            "PREFERS": Cardinality.MANY,
            "WORKS_FOR": Cardinality.MANY,
            "INTERESTED_IN": Cardinality.MANY,
            "VISITED": Cardinality.MANY,
            "DISLIKES": Cardinality.MANY,
            "LIKES": Cardinality.MANY,
            "ALLERGIC_TO": Cardinality.MANY,
            "INFECTED_WITH": Cardinality.MANY,
            "CONTRIBUTES_TO": Cardinality.MANY,
            "CONTRIBUTED_TO": Cardinality.MANY,
            "HAS_ADMIN_ACCESS": Cardinality.MANY,
            "DEPENDS_ON": Cardinality.MANY,
            "CONNECTS_TO": Cardinality.MANY,
            "OWNS": Cardinality.MANY,
            "HAS_SKILL": Cardinality.MANY,
            "ASSIGNED_TO": Cardinality.MANY,
            "CONTAINS": Cardinality.MANY,
            "MANAGES": Cardinality.MANY,
            "MANAGED_BY": Cardinality.MANY,
            "PARENT_OF": Cardinality.MANY,
            "CHILD_OF": Cardinality.MANY,
            "MEMBER_OF": Cardinality.MANY,
            "AUTHORS": Cardinality.MANY,
            "OPERATES": Cardinality.MANY,
            "PROFICIENT_IN": Cardinality.MANY,
            "EXHIBITS": Cardinality.MANY,
            "STORED_FACT": Cardinality.MANY,

            # Project, Architecture, and Technical Specification Predicates
            "LEAD_ARCHITECT": Cardinality.ONE,
            "ARCHITECT_OF": Cardinality.MANY,
            "HAS_LEAD_ARCHITECT": Cardinality.ONE,
            "DEPLOYED_ON": Cardinality.ONE,
            "DEPLOYED_TO": Cardinality.ONE,
            "HOSTED_ON": Cardinality.ONE,
            "RUNS_ON": Cardinality.ONE,
            "RUNS": Cardinality.ONE,
            "USES": Cardinality.ONE,
            "DATABASE_ENGINE": Cardinality.ONE,
            "DATABASE": Cardinality.ONE,
            "CODENAME": Cardinality.ONE,
            "TYPE": Cardinality.MANY,
            "USES_LANGUAGE": Cardinality.MANY,
            "USES_FRAMEWORK": Cardinality.MANY,
            "HAS_SLA": Cardinality.MANY,
            "LATENCY_SLA": Cardinality.ONE,
            "SLA_REQUIREMENT": Cardinality.MANY,
            "CUTOVER_DATE": Cardinality.ONE,
            "PRODUCTION_CUTOVER_DATE": Cardinality.ONE,
            "LAUNCH_DATE": Cardinality.ONE,
            "RELEASE_DATE": Cardinality.ONE,
            "CONFIGURED_WITH": Cardinality.MANY,
            "PARAMETER_OF": Cardinality.MANY,
            "TARGETS_REGION": Cardinality.MANY,
            "PLANNED_TARGET": Cardinality.MANY,
            "MIGRATION_STATUS": Cardinality.ONE,
            "APPROVED_MIGRATION": Cardinality.MANY,
            "ROUTES_TO": Cardinality.MANY,
            "COMPLIES_WITH": Cardinality.MANY,
            "LEADS": Cardinality.ONE,
        }

        # Canonical aliases
        self._aliases: Dict[str, str] = {
            "WORKS_AT": "WORKS_FOR",
            "EMPLOYED_BY": "WORKS_FOR",
            "LIVES_AT": "LIVES_IN",
            "RESIDES_IN": "LIVES_IN",
            "LOCATED_AT": "LOCATED_IN",
            "AUTHORED": "AUTHORS",
            "EMAIL": "PRIMARY_EMAIL",
            "HAS_PRIMARY_EMAIL": "PRIMARY_EMAIL",
            "NAME": "HAS_NAME",
            "FAVORITE": "LIKES",
            "LOVES": "LIKES",
            "ENJOYS": "LIKES",
            "CONTRIBUTE_TO": "CONTRIBUTES_TO",
            "ARCHITECT": "LEAD_ARCHITECT",
            "HEAD_OF_ARCHITECTURE": "LEAD_ARCHITECT",
            "USES_DATABASE": "DATABASE_ENGINE",
            "PRIMARY_DATABASE": "DATABASE_ENGINE",
            "DATABASE": "DATABASE_ENGINE",
            "USES_FRAMEWORK": "USES",
            "LATENCY_SLA": "HAS_SLA",
            "SLA_REQUIREMENT": "HAS_SLA",
            "CUTOVER_DATE": "PRODUCTION_CUTOVER_DATE",
            "LAUNCH_DATE": "PRODUCTION_CUTOVER_DATE",
            "RELEASE_DATE": "PRODUCTION_CUTOVER_DATE",
            "RUNS_ON": "DEPLOYED_ON",
            "HOSTED_ON": "DEPLOYED_ON",
            "DEPLOYED_TO": "DEPLOYED_ON",
            "PLANNED_DATABASE": "PLANNED_TARGET",
            "PLANNED_MIGRATION": "PLANNED_TARGET",
            "MIGRATES_TO": "PLANNED_TARGET",
            "MIGRATION_APPROVED": "APPROVED_MIGRATION",
            "UPGRADED_TO": "RUNS",
            "MIGRATED_TO": "RUNS",
            "SWITCHED_TO": "RUNS",
            "OPERATES": "RUNS",
        }

        # Inverses
        self._inverses: Dict[str, str] = {
            "MANAGES": "MANAGED_BY",
            "MANAGED_BY": "MANAGES",
            "PARENT_OF": "CHILD_OF",
            "CHILD_OF": "PARENT_OF",
            "OWNS": "OWNED_BY",
            "OWNED_BY": "OWNS",
        }

    def normalize(self, predicate: str) -> str:
        """Converts predicate string into standard uppercase canonical identifier."""
        norm = predicate.strip().replace(" ", "_").replace("-", "_").upper()
        return self._aliases.get(norm, norm)

    def is_registered(self, predicate: str) -> bool:
        """Checks if a predicate or its normalized alias is recognized by the registry."""
        norm = self.normalize(predicate)
        return norm in self._cardinality

    def get_cardinality(self, predicate: str) -> Cardinality:
        """Returns Cardinality.ONE or Cardinality.MANY (defaults to MANY if unknown)."""
        norm = self.normalize(predicate)
        return self._cardinality.get(norm, Cardinality.MANY)

    def get_inverse(self, predicate: str) -> Optional[str]:
        """Returns the inverse predicate if known."""
        norm = self.normalize(predicate)
        return self._inverses.get(norm)

    def register(
        self,
        predicate: str,
        cardinality: Cardinality,
        aliases: Optional[List[str]] = None,
        inverse: Optional[str] = None
    ) -> None:
        """Registers a custom predicate."""
        norm = predicate.strip().replace(" ", "_").replace("-", "_").upper()
        self._cardinality[norm] = cardinality
        if aliases:
            for a in aliases:
                self._aliases[a.strip().replace(" ", "_").replace("-", "_").upper()] = norm
        if inverse:
            inv_norm = inverse.strip().replace(" ", "_").replace("-", "_").upper()
            self._inverses[norm] = inv_norm
            self._inverses[inv_norm] = norm

    def list_predicates(self) -> List[Dict[str, str]]:
        """Returns all registered predicates with their cardinality."""
        return [
            {
                "name": name,
                "cardinality": card.value,
                "description": f"{'Functional (one-to-one)' if card == Cardinality.ONE else 'Relational (one-to-many)'} relation predicate."
            }
            for name, card in sorted(self._cardinality.items())
        ]


predicate_registry = PredicateRegistry()
