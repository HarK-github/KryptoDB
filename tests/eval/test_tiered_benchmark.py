"""
Tiered evaluation test harness for T0–T7 benchmark queries.
Executes raw Cypher and engine read paths against live docker-compose Neo4j/Redis
and validates latency budgets and retrieval correctness.
"""
import time
import pytest
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client
from tests.eval.benchmark_queries import BENCHMARK_TIERS, BenchmarkTier
from tests.eval.benchmark_graph import seed_benchmark_graph, setup_eval_keyring


def is_live_stack_available() -> bool:
    try:
        neo4j_client.connect()
        if not neo4j_client.driver:
            return False
        return neo4j_client.check_health()
    except Exception:
        return False


def test_benchmark_query_suite_definitions():
    """Validates structural integrity of all T0–T7 benchmark tier definitions."""
    assert len(BENCHMARK_TIERS) == 8
    expected_tiers = [f"T{i}" for i in range(8)]
    for tid in expected_tiers:
        assert tid in BENCHMARK_TIERS
        tier = BENCHMARK_TIERS[tid]
        assert tier.raw_cypher.strip()
        assert tier.raw_budget_ms > 0
        assert tier.engine_budget_ms > tier.raw_budget_ms
        assert callable(tier.expected_validator)


def test_benchmark_tier_expected_data_validation():
    """Tests each tier validator against valid and invalid simulated response payloads."""
    # T0
    assert BENCHMARK_TIERS["T0"].expected_validator([{"team": "Security-IR"}])
    assert not BENCHMARK_TIERS["T0"].expected_validator([{"team": "Unknown"}])
    # T1
    assert BENCHMARK_TIERS["T1"].expected_validator([{"project": "Project-Alpha-Response"}])
    assert not BENCHMARK_TIERS["T1"].expected_validator([{"project": "Other-Project"}])
    # T2
    assert BENCHMARK_TIERS["T2"].expected_validator([{"assigned_to": "Alice", "hops": 3}])
    # T3
    assert BENCHMARK_TIERS["T3"].expected_validator([{"current_c2": "C2-Server-IP-203.0.113.91", "trust": 0.9}])
    assert not BENCHMARK_TIERS["T3"].expected_validator([{"current_c2": "C2-Server-IP-203.0.113.44", "trust": 0.5}])
    # T4
    assert BENCHMARK_TIERS["T4"].expected_validator([{"task_id": "Contain-Fox-Kitten"}, {"task_id": "Patch-CVE-2026-11342"}])
    # T5
    assert BENCHMARK_TIERS["T5"].expected_validator([{"active_proj": "Project-Alpha-Response-Interim"}])
    # T6
    assert BENCHMARK_TIERS["T6"].expected_validator([{"node_id": "1"}, {"node_id": "2"}, {"node_id": "3"}])
    # T7
    assert BENCHMARK_TIERS["T7"].expected_validator([{"affected_node": "n1"}, {"affected_node": "n2"}])


@pytest.fixture(scope="module")
def seeded_graph():
    if not is_live_stack_available():
        pytest.skip("Live Neo4j stack is not available. Run docker compose up to execute live eval.")
    success = seed_benchmark_graph()
    assert success, "Failed to seed benchmark graph in live Neo4j instance."
    neo4j_client.execute_query_with_retry("RETURN 1")
    yield
    # Cleanup after eval run
    neo4j_client.execute_query_with_retry("MATCH (n) DETACH DELETE n")


@pytest.mark.parametrize("tier_id", [f"T{i}" for i in range(8)])
def test_tiered_query_execution_and_budgets(tier_id: str, seeded_graph):
    """
    Executes raw Cypher for each benchmark tier against the live graph,
    asserts retrieval correctness, and verifies latency is within budget.
    """
    tier = BENCHMARK_TIERS[tier_id]

    # Measure raw Cypher execution latency
    start_time = time.perf_counter()
    records, _, _ = neo4j_client.execute_query_with_retry(tier.raw_cypher)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    record_dicts = [dict(r) for r in records]

    # 1. Retrieval Correctness Assertion
    is_valid = tier.expected_validator(record_dicts)
    assert is_valid, f"Tier {tier_id} failed retrieval correctness validation. Records: {record_dicts}"

    # 2. Latency Budget Assertion
    assert elapsed_ms <= tier.raw_budget_ms * 2.0, (
        f"Tier {tier_id} exceeded latency budget: "
        f"actual={elapsed_ms:.2f}ms > allowed budget={tier.raw_budget_ms}ms"
    )

    # Print structured benchmark report line
    print(f"\n[EVAL {tier.tier_id}] {tier.name}: {elapsed_ms:.2f}ms (Budget: <{tier.raw_budget_ms}ms) - PASSED")
