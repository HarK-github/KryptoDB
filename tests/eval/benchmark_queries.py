"""
Query suite definitions and verification for T0–T7 evaluation tiers.
Formalizes Section 2 of TESTING.md with exact query text, budgets, and validation logic.
"""
from dataclasses import dataclass
from typing import Callable, Any, Dict, List

@dataclass
class BenchmarkTier:
    tier_id: str
    name: str
    description: str
    example_prompt: str
    raw_cypher: str
    raw_budget_ms: float
    engine_budget_ms: float
    expected_validator: Callable[[List[Any]], bool]

BENCHMARK_TIERS: Dict[str, BenchmarkTier] = {
    "T0": BenchmarkTier(
        tier_id="T0",
        name="Single-hop exact lookup",
        description="Lookup direct relationship of a single entity.",
        example_prompt="What team is Alice on?",
        raw_cypher="""
        MATCH (:PERSON {id:"Alice"})<-[:HAS_MEMBER]-(t:TEAM)
        RETURN t.id AS team
        """,
        raw_budget_ms=20.0,
        engine_budget_ms=300.0,
        expected_validator=lambda records: any(
            r.get("team") == "Security-IR" or (isinstance(r, dict) and "Security-IR" in str(r.values()))
            for r in records
        )
    ),
    "T1": BenchmarkTier(
        tier_id="T1",
        name="Two-hop path traversal",
        description="Traverse two hops from member through team to division/project.",
        example_prompt="Who leads the project Alice's team owns?",
        raw_cypher="""
        MATCH (:PERSON {id:"Alice"})<-[:HAS_MEMBER]-(:TEAM)<-[:HAS_TEAM]-(:DIVISION),
              (:PERSON {id:"Alice"})-[:LEADS]->(p:PROJECT)
        RETURN p.id AS project
        """,
        raw_budget_ms=50.0,
        engine_budget_ms=500.0,
        expected_validator=lambda records: any(
            r.get("project") == "Project-Alpha-Response" or "Project-Alpha-Response" in str(r)
            for r in records
        )
    ),
    "T2": BenchmarkTier(
        tier_id="T2",
        name="3–4 hop lineage traversal",
        description="Multi-hop derived_from lineage chain across partitions.",
        example_prompt="Trace what the Fox-Kitten finding led to, all the way to who it was assigned to.",
        raw_cypher="""
        MATCH path = (:MALWARE_FAMILY {id:"Fox-Kitten"})-[:EXPLOITS]->()
                     -[*0..3]->(:TASK)-[:ASSIGNED_TO]->(person)
        RETURN person.id AS assigned_to, length(path) AS hops
        """,
        raw_budget_ms=150.0,
        engine_budget_ms=800.0,
        expected_validator=lambda records: any(
            r.get("assigned_to") == "Alice" or "Alice" in str(r)
            for r in records
        )
    ),
    "T3": BenchmarkTier(
        tier_id="T3",
        name="Contested-fact arbitration",
        description="Retrieve current active fact excluding superseded conflicting edge.",
        example_prompt="What's ACME Corp's current C2 server IP, and was there ever a conflicting report?",
        raw_cypher="""
        MATCH (:ORGANIZATION {id:"ACME-Corp"})-[r:HOSTED_ON]->(ip)
        WHERE r.valid_to IS NULL
        RETURN ip.id AS current_c2, r.source_agent_id AS agent, r.trust_weight AS trust
        """,
        raw_budget_ms=100.0,
        engine_budget_ms=500.0,
        expected_validator=lambda records: (
            len(records) == 1 and
            any("203.0.113.91" in str(r) for r in records) and
            not any("203.0.113.44" in str(r) for r in records)
        )
    ),
    "T4": BenchmarkTier(
        tier_id="T4",
        name="Fan-out aggregation",
        description="Aggregates tasks across multiple teams under a division.",
        example_prompt="List every task under any project anyone on the Security division is working on.",
        raw_cypher="""
        MATCH (:DIVISION {id:"ACME-Security"})-[:HAS_TEAM]->(:TEAM)-[:HAS_MEMBER]->(:PERSON)
              -[:LEADS|WORKS_ON]->(:PROJECT)-[:HAS_TASK]->(task)
        RETURN DISTINCT task.id AS task_id
        """,
        raw_budget_ms=300.0,
        engine_budget_ms=1200.0,
        expected_validator=lambda records: (
            len(records) >= 2 and
            any("Contain-Fox-Kitten" in str(r) for r in records) and
            any("Patch-CVE-2026-11342" in str(r) for r in records)
        )
    ),
    "T5": BenchmarkTier(
        tier_id="T5",
        name="Temporal 'as-of' query",
        description="Time-travel query evaluating valid_from <= date <= valid_to.",
        example_prompt="What was Bob working on as of July 10th, 2026?",
        raw_cypher="""
        MATCH (:PERSON {id:"Bob"})-[r:WORKS_ON]->(proj)
        WHERE r.valid_from <= date("2026-07-10") AND (r.valid_to IS NULL OR r.valid_to > date("2026-07-10"))
        RETURN proj.id AS active_proj
        """,
        raw_budget_ms=100.0,
        engine_budget_ms=600.0,
        expected_validator=lambda records: any(
            "Project-Alpha-Response-Interim" in str(r) for r in records
        )
    ),
    "T6": BenchmarkTier(
        tier_id="T6",
        name="Cross-partition scoped query",
        description="Traverse 2-hop neighborhood spanning multiple partitions.",
        example_prompt="Summarize everything connected to Project Alpha Response, across whatever's currently unlocked.",
        raw_cypher="""
        MATCH (proj:PROJECT {id:"Project-Alpha-Response"})-[*0..2]-(n)
        RETURN DISTINCT n.id AS node_id, n.permission_scope AS scope
        """,
        raw_budget_ms=400.0,
        engine_budget_ms=1500.0,
        expected_validator=lambda records: len(records) >= 3
    ),
    "T7": BenchmarkTier(
        tier_id="T7",
        name="Cascade dry-run",
        description="Trace potential retraction impact along lineage path without committing changes.",
        example_prompt="If the Fox-Kitten finding were retracted, what would that affect?",
        raw_cypher="""
        MATCH (root {id:"CVE-2026-11342"})<-[:EXPLOITS]-(fk:MALWARE_FAMILY)
        MATCH path = (fk)-[r*1..4]->(descendant)
        WHERE ALL(rel IN r WHERE rel.derived_from IS NOT NULL OR rel.id = "e-root")
        RETURN DISTINCT descendant.id AS affected_node, length(path) AS depth
        """,
        raw_budget_ms=250.0,
        engine_budget_ms=1000.0,
        expected_validator=lambda records: len(records) >= 2
    ),
}
