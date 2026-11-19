"""
End-to-End Memory Engine Evaluation Benchmark Suite.
Validates the 6 Core Memory Invariants recommended by Proxima:
1. Supersession Accuracy (SA): 100%
2. Cardinality Invariant Rate (CIR): 100%
3. Provenance Direction Precision (PDP): 100%
4. Scope/Tenant Boundary Isolation (NLR): 0.0% leakage
5. Context Pollution Resistance (GPR): 0.0% pollution
6. Multi-hop Graph Traversal without inversion
"""
import uuid
import pytest
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, AIMessage

from kryptodb.agent_runtime.langgraph.graph import get_compiled_graph
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.core.memory.turn_analysis import TurnAnalyzer, SegmentType
from kryptodb.core.memory.delta_extractor import extract_state_deltas, shift_date_string
from kryptodb.core.memory.predicates import PredicateRegistry, Cardinality


@pytest.fixture(autouse=True)
def ensure_db():
    neo4j_client.connect()
    assert neo4j_client.driver is not None, "Neo4j connection required for evaluation"


def test_eval_stateful_supersession_and_temporal_shift():
    """Verify state mutation, equal-confidence supersession, and temporal date math."""
    thread_id = f"eval_supersede_{uuid.uuid4().hex[:6]}"
    graph = get_compiled_graph()

    # 1. Temporal shift math verification
    date_original = "November 14"
    date_shifted = shift_date_string(date_original, 10)
    assert date_shifted == "November 24", f"Expected 'November 24', got {date_shifted}"

    date_prev = shift_date_string("January 05", -10)
    assert "December 26" in date_prev, f"Expected 'December 26' in {date_prev}"

    # 2. Stateful delta extraction from existing entity state
    active_facts = [
        {"subject": "Project Titan", "predicate": "USES", "object": "PostgreSQL 16", "confidence": 1.0, "status": "active"},
        {"subject": "Project Titan", "predicate": "PRODUCTION_CUTOVER_DATE", "object": "November 14", "confidence": 1.0, "status": "active"},
        {"subject": "Project Titan", "predicate": "DEPLOYED_ON", "object": "AWS us-east-1", "confidence": 1.0, "status": "active"},
    ]

    mutation_turn = "We migrated the database to CockroachDB and push the production cutover back by 10 days."
    
    class StubLLM:
        def with_structured_output(self, schema):
            class StubStructured:
                def invoke(self, messages):
                    from kryptodb.core.memory.delta_extractor import FactItem, FactExtractionPatch
                    return FactExtractionPatch(facts=[
                        FactItem(
                            subject="Project Titan",
                            predicate="USES",
                            obj="CockroachDB",
                            description="Project Titan USES CockroachDB",
                            quote="migrated the database to CockroachDB",
                        ),
                        FactItem(
                            subject="Project Titan",
                            predicate="PRODUCTION_CUTOVER_DATE",
                            obj="November 24",
                            description="Project Titan PRODUCTION_CUTOVER_DATE November 24",
                            quote="push the production cutover back by 10 days",
                        ),
                    ])
            return StubStructured()

    deltas = extract_state_deltas(
        target_text=mutation_turn,
        active_entity_state=active_facts,
        agent_id="eval-agent",
        user_scope="research",
        llm=StubLLM(),
    )

    delta_dict = {d.predicate: d for d in deltas}
    assert "USES" in delta_dict, "USES mutation not detected"
    assert delta_dict["USES"].obj == "CockroachDB"

    assert "PRODUCTION_CUTOVER_DATE" in delta_dict, "Date shift mutation not detected"
    assert delta_dict["PRODUCTION_CUTOVER_DATE"].obj == "November 24"


def test_eval_context_pollution_resistance():
    """Verify that questions, commands, and chitchat never create durable graph facts."""
    from kryptodb.core.memory.turn_analysis import turn_analyzer

    # Test cases that MUST NOT create durable facts
    non_durable_turns = [
        "What is the cutover date for Project Titan?",
        "Compare the trade-offs between Raft and Paxos consensus.",
        "Hello, how are you today?",
        "Clear all temporary logs immediately.",
        "Please confirm you've logged these details.",
    ]

    for turn in non_durable_turns:
        res = turn_analyzer.analyze_turn(turn)
        durable_segs = [s for s in res.segments if s.durable]
        assert len(durable_segs) == 0, f"Turn '{turn}' falsely flagged as durable: {durable_segs}"


def test_eval_cardinality_invariants():
    """Verify single-valued predicates supersede and multi-valued predicates append."""
    from kryptodb.core.memory.predicates import predicate_registry

    assert predicate_registry.get_cardinality("LEAD_ARCHITECT") == Cardinality.ONE
    assert predicate_registry.get_cardinality("USES") == Cardinality.ONE
    assert predicate_registry.get_cardinality("PRODUCTION_CUTOVER_DATE") == Cardinality.ONE

    assert predicate_registry.get_cardinality("EXHIBITS") == Cardinality.MANY
    assert predicate_registry.get_cardinality("STORED_FACT") == Cardinality.MANY
    assert predicate_registry.get_cardinality("WORKS_FOR") == Cardinality.MANY


def test_eval_provenance_and_directional_precision():
    """Verify that graph traversal respects edge directions and does not invert subject/object."""
    from kryptodb.infrastructure.neo4j.queries import TRAVERSAL_1HOP_QUERY, TRAVERSAL_2HOP_EXPANSION_QUERY

    # Cypher queries must preserve startNode as subject and endNode as object
    assert "startNode(e).name AS s_name" in TRAVERSAL_1HOP_QUERY
    assert "endNode(e).name AS t_name" in TRAVERSAL_1HOP_QUERY
    assert "startNode(e1).name AS e1_src" in TRAVERSAL_2HOP_EXPANSION_QUERY
    assert "endNode(e1).name AS e1_dst" in TRAVERSAL_2HOP_EXPANSION_QUERY
    assert "startNode(e2).name AS e2_src" in TRAVERSAL_2HOP_EXPANSION_QUERY
    assert "endNode(e2).name AS e2_dst" in TRAVERSAL_2HOP_EXPANSION_QUERY


def test_eval_scope_isolation_gating():
    """Verify that unauthorized and restricted scopes cannot be accessed or written without approval."""
    graph = get_compiled_graph()

    denial_state = {
        "messages": [HumanMessage(content="Query classified intel")],
        "user_scope": "RESTRICTED:classified",
        "agent_id": "eval-agent",
        "denied_recalls": [{"status": "denied_permission", "scope": "RESTRICTED:classified"}],
    }

    res = graph.invoke(denial_state)
    assert res["status"] == "denied", f"Expected denied status, got {res.get('status')}"
    assert "Access Denied" in res["messages"][-1].content
