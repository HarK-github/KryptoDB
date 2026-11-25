"""Steps 2 & 3 Verification Gate: Honest claims, WORKS_FOR cardinality, confidence conflicts, norm_name, scoped revoke."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.conflicts import ConflictAction, ConflictEngine
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.memory.predicates import Cardinality, predicate_registry
from kryptodb.infrastructure.neo4j.queries import (
    FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY,
    REVOKE_FACT_QUERY,
    WRITE_FACT_QUERY,
)
from kryptodb.infrastructure.neo4j.store import _norm_entity_name


class _StubStore:
    def __init__(self, active: List[MemoryRecord]):
        self._active = active

    def find_active_by_subject_predicate(self, subject: str, predicate: str, context: RequestContext) -> List[MemoryRecord]:
        norm_s = _norm_entity_name(subject)
        return [
            r for r in self._active
            if _norm_entity_name(r.subject) == norm_s and r.predicate == predicate.upper()
        ]


def test_works_for_is_cardinality_one():
    assert predicate_registry.get_cardinality("WORKS_FOR") == Cardinality.ONE


def test_confidence_aware_conflict_resolution_and_case_normalization():
    ctx = RequestContext.create(tenant_id="acme", agent_id="agent-1", active_scopes=["research"])
    existing = MemoryRecord(
        id="fact-high-conf",
        subject="Alice",
        predicate="LIVES_IN",
        object="London",
        scope="research",
        tenant_id="acme",
        confidence=0.95,
        status=MemoryStatus.ACTIVE,
        version=1,
    )
    engine = ConflictEngine(store=_StubStore([existing]))

    # Case-insensitive subject ("alice" matches "Alice"), lower confidence -> FLAG_CONFLICT
    low_res = engine.evaluate(subject="  alice  ", predicate="LIVES_IN", obj="Paris", confidence=0.4, context=ctx)
    assert low_res.action == ConflictAction.FLAG_CONFLICT
    assert low_res.existing_fact_id == "fact-high-conf"

    # Explicit mutation overrides even at lower confidence
    explicit_res = engine.evaluate(
        subject="alice", predicate="LIVES_IN", obj="Paris", confidence=0.4, context=ctx, is_explicit_mutation=True
    )
    assert explicit_res.action == ConflictAction.SUPERSEDE_AND_CREATE
    assert explicit_res.supersede_fact_id == "fact-high-conf"
    assert explicit_res.version == 2

    # Higher/equal confidence supersedes normally
    high_res = engine.evaluate(subject="ALICE", predicate="LIVES_IN", obj="Berlin", confidence=0.95, context=ctx)
    assert high_res.action == ConflictAction.SUPERSEDE_AND_CREATE
    assert high_res.version == 2


def test_cypher_queries_enforce_norm_name_lock_and_scoped_complete_revoke():
    assert "norm_name: $s_norm_name" in WRITE_FACT_QUERY
    assert "SET s._write_lock = true" in WRITE_FACT_QUERY
    assert "s.norm_name = $norm_subject" in FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY
    assert "$is_system = true OR r.scope IN $active_scopes" in REVOKE_FACT_QUERY
    assert "r.description = NULL" in REVOKE_FACT_QUERY
    assert "r.embedding = NULL" in REVOKE_FACT_QUERY
    assert "r.predicate = 'REVOKED'" in REVOKE_FACT_QUERY


def test_no_honesty_regressions_in_readme_ui_or_graph():
    root = Path(__file__).resolve().parents[3]
    graph_txt = (root / "kryptodb/agent_runtime/langgraph/graph.py").read_text(encoding="utf-8").lower()
    for banned in ("project titan", "elena rostova", "cockroachdb"):
        assert banned not in graph_txt, f"Found banned hardcoded string '{banned}' in graph.py"

    layout_txt = (root / "ui/src/app/layout.tsx").read_text(encoding="utf-8")
    assert "Crypto-Enforced" not in layout_txt

    readme_txt = (root / "README.md").read_text(encoding="utf-8")
    assert "dev-admin-key" not in readme_txt
    assert "1.2273" not in readme_txt


if __name__ == "__main__":
    test_works_for_is_cardinality_one()
    test_confidence_aware_conflict_resolution_and_case_normalization()
    test_cypher_queries_enforce_norm_name_lock_and_scoped_complete_revoke()
    test_no_honesty_regressions_in_readme_ui_or_graph()
    print("ALL STEP 2 & STEP 3 SEMANTICS TESTS PASSED (4/4)")
