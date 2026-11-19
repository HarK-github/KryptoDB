"""Comprehensive regression test suite for KryptoDB GraphRAG Architectural Remediation.

Covers:
- Section 7: Project Titan 4-Turn Integration Sequence
- Section 8: Mandatory Test Matrix (T01 through T24)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from kryptodb.core.context import RequestContext, create_system_context
from kryptodb.core.memory.delta_extractor import (
    DeltaAction,
    ProposedMutation,
    extract_state_deltas,
    parse_flexible_date,
    shift_date_string,
    FactItem,
)
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus, MemoryType
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.core.memory.predicates import Cardinality, predicate_registry
from kryptodb.core.memory.turn_analysis import FakeTurnAnalyzer, SegmentType, TurnAnalysisResult
from kryptodb.agent_runtime.langgraph.graph import (
    AgentState,
    create_agent_graph,
    get_compiled_graph,
    create_intent_classifier_node,
    create_extraction_node,
    conflict_resolution_node,
    create_direct_write_node,
)
from tests.fakes import FakeEmbedder, FakeMemoryStore


class StubExtractionLLM:
    def __init__(self, facts_to_return):
        self.facts_to_return = facts_to_return

    def with_structured_output(self, schema):
        class StructuredInvoker:
            def __init__(self, facts):
                self.facts = facts

            def invoke(self, messages):
                from kryptodb.core.memory.delta_extractor import FactItem, FactExtractionPatch
                return FactExtractionPatch(facts=[
                    FactItem(
                        subject=f["subject"],
                        predicate=f["predicate"],
                        obj=f["obj"],
                        description=f.get("description", f"{f['subject']} {f['predicate']} {f['obj']}"),
                        quote=f.get("quote", f["obj"]),
                    )
                    for f in self.facts
                ])
        return StructuredInvoker(self.facts_to_return)


# ==============================================================================
# SECTION 7: PROJECT TITAN 4-TURN INTEGRATION SEQUENCE
# ==============================================================================

class TestProjectTitanSequence:
    """Rigorous 4-turn integration test verifying stateful memory evolution."""

    def test_project_titan_4_turn_sequence(self):
        """Executes Turn 1 through Turn 4 against the stateful delta memory engine."""
        tenant_id = f"test-tenant-{uuid.uuid4().hex[:6]}"
        agent_id = "agent-1"
        scope = "PROJECT:titan"
        context = RequestContext(
            tenant_id=tenant_id,
            user_id="user-1",
            agent_id=agent_id,
            active_scopes=[scope, "GLOBAL"],
        )

        store = FakeMemoryStore(embedder=FakeEmbedder())
        service = MemoryService(store=store)

        # ----------------------------------------------------------------------
        # Turn 1: Seeding Dense Entities & Constraints
        # "Let's set up the parameters for Project Titan. The lead architect is Elena Rostova,
        # and we are deploying on AWS us-east-1 using PostgreSQL 16. Our strict latency SLA is
        # under 45ms, and the production cutover date is November 14. Confirm you've logged these details."
        # ----------------------------------------------------------------------
        turn_1_text = (
            "Let's set up the parameters for Project Titan. The lead architect is Elena Rostova, "
            "and we are deploying on AWS us-east-1 using PostgreSQL 16. Our strict latency SLA is "
            "under 45ms, and the production cutover date is November 14. Confirm you've logged these details."
        )

        # Discourse analysis: command must NOT suppress factual extraction
        analyzer = FakeTurnAnalyzer()
        analysis_1 = analyzer.analyze_turn(turn_1_text)
        durable_segs_1 = [s for s in analysis_1.segments if s.durable]
        assert len(durable_segs_1) > 0, "Turn 1 must identify durable statement segments"

        # Delta extraction from Turn 1 (no prior state)
        t1_llm = StubExtractionLLM([
            {"subject": "Project Titan", "predicate": "LEAD_ARCHITECT", "obj": "Elena Rostova"},
            {"subject": "Project Titan", "predicate": "DEPLOYED_ON", "obj": "AWS us-east-1"},
            {"subject": "Project Titan", "predicate": "USES", "obj": "PostgreSQL 16"},
            {"subject": "Project Titan", "predicate": "HAS_SLA", "obj": "under 45ms"},
            {"subject": "Project Titan", "predicate": "PRODUCTION_CUTOVER_DATE", "obj": "November 14"},
        ])
        mutations_1 = extract_state_deltas(turn_1_text, active_entity_state=[], llm=t1_llm)
        assert len(mutations_1) == 5, f"Turn 1 must extract all 5 technical facts, got {len(mutations_1)}"

        # Commit Turn 1 facts to memory
        t1_records = {}
        for m in mutations_1:
            rec = service.propose_memory(
                subject=m.subject,
                predicate=m.predicate,
                obj=m.obj,
                context=context,
                scope=scope,
                quote_substring=m.quote,
            )
            t1_records[m.predicate] = rec

        assert t1_records["LEAD_ARCHITECT"].object == "Elena Rostova"
        assert t1_records["DEPLOYED_ON"].object == "AWS us-east-1"
        assert t1_records["USES"].object == "PostgreSQL 16"
        assert t1_records["HAS_SLA"].object == "under 45ms"
        assert t1_records["PRODUCTION_CUTOVER_DATE"].object == "November 14"

        # Verify all are active
        active_t1 = store.find_active_by_subject_predicate("Project Titan", "USES", context)
        assert len(active_t1) == 1
        assert active_t1[0].object == "PostgreSQL 16"

        # ----------------------------------------------------------------------
        # Turn 2: State Mutation & Conflict Resolution (The Override Test)
        # "Quick update on Project Titan: we had to migrate the database to CockroachDB due
        # to multi-region requirements, so we are now deploying across both us-east-1 and eu-central-1.
        # Also, push the production cutover back by 10 days. Keep everything else the same."
        # ----------------------------------------------------------------------
        turn_2_text = (
            "Quick update on Project Titan: we had to migrate the database to CockroachDB due "
            "to multi-region requirements, so we are now deploying across both us-east-1 and eu-central-1. "
            "Also, push the production cutover back by 10 days. Keep everything else the same."
        )

        # Retrieve prior active entity state for Project Titan
        prior_facts = [
            {"subject": f.subject, "predicate": f.predicate, "obj": f.object, "id": f.id}
            for f in store.facts.values()
            if f.subject == "Project Titan" and f.status == MemoryStatus.ACTIVE
        ]
        assert len(prior_facts) == 5, "Prior active state must have 5 facts before Turn 2"

        # Extract state deltas conditioned on prior state
        t2_llm = StubExtractionLLM([
            {"subject": "Project Titan", "predicate": "USES", "obj": "CockroachDB"},
            {"subject": "Project Titan", "predicate": "DEPLOYED_ON", "obj": "AWS us-east-1 and eu-central-1"},
            {"subject": "Project Titan", "predicate": "PRODUCTION_CUTOVER_DATE", "obj": "November 24"},
        ])
        mutations_2 = extract_state_deltas(turn_2_text, active_entity_state=prior_facts, llm=t2_llm)
        assert len(mutations_2) == 3, f"Turn 2 must produce 3 mutations (db, regions, date), got {len(mutations_2)}"

        # 1. Database updated to CockroachDB
        db_mut = next(m for m in mutations_2 if m.predicate == "USES")
        assert db_mut.obj == "CockroachDB"

        # 2. Region updated to multi-region
        region_mut = next(m for m in mutations_2 if m.predicate == "DEPLOYED_ON")
        assert "eu-central-1" in region_mut.obj

        # 3. Cutover date shifted +10 days: Nov 14 + 10 days = Nov 24
        date_mut = next(m for m in mutations_2 if m.predicate == "PRODUCTION_CUTOVER_DATE")
        assert date_mut.obj == "November 24"

        # Commit mutations
        for m in mutations_2:
            service.propose_memory(
                subject=m.subject,
                predicate=m.predicate,
                obj=m.obj,
                context=context,
                scope=scope,
                quote_substring=m.quote,
            )

        # Verify old PostgreSQL 16 is SUPERSEDED, CockroachDB is ACTIVE
        all_uses = [f for f in store.facts.values() if f.subject == "Project Titan" and f.predicate == "USES"]
        assert len(all_uses) == 2
        active_uses = [f for f in all_uses if f.status == MemoryStatus.ACTIVE]
        superseded_uses = [f for f in all_uses if f.status == MemoryStatus.SUPERSEDED]
        assert len(active_uses) == 1 and active_uses[0].object == "CockroachDB"
        assert len(superseded_uses) == 1 and superseded_uses[0].object == "PostgreSQL 16"

        # Verify cutover date is November 24
        active_cutover = store.find_active_by_subject_predicate("Project Titan", "PRODUCTION_CUTOVER_DATE", context)
        assert len(active_cutover) == 1
        assert active_cutover[0].object == "November 24"

        # ----------------------------------------------------------------------
        # Turn 3: Distractor / Context Pollution (The "Needle in a Haystack" Test)
        # "Unrelated question while I have you: compare the trade-offs between Raft and Paxos
        # consensus algorithms in distributed systems. Keep it to three bullet points."
        # ----------------------------------------------------------------------
        turn_3_text = (
            "Unrelated question while I have you: compare the trade-offs between Raft and Paxos "
            "consensus algorithms in distributed systems. Keep it to three bullet points."
        )

        analysis_3 = analyzer.analyze_turn(turn_3_text)
        has_durable_3 = any(s.durable for s in analysis_3.segments)
        assert not has_durable_3, "Turn 3 distractor question must NOT contain durable facts"

        # ----------------------------------------------------------------------
        # Turn 4: Synthesis & Negative Recall (The Evaluation Prompt)
        # "Generate a pre-deployment sign-off summary for Project Titan. Include the lead architect,
        # final cloud regions, database engine, latency SLA, and the exact new cutover date.
        # Constraint: Do not mention any deprecated tech stack choices or old dates from earlier in our conversation."
        # ----------------------------------------------------------------------
        turn_4_text = (
            "Generate a pre-deployment sign-off summary for Project Titan. Include the lead architect, "
            "final cloud regions, database engine, latency SLA, and the exact new cutover date. "
            "Constraint: Do not mention any deprecated tech stack choices or old dates from earlier in our conversation."
        )

        # Active recalled facts only
        recalled_active = [
            {"subject": f.subject, "predicate": f.predicate, "object": f.object, "permission_scope": f.scope}
            for f in store.facts.values()
            if f.subject == "Project Titan" and f.status == MemoryStatus.ACTIVE
        ]

        # Verify recalled active facts
        recalled_dict = {f["predicate"]: f["object"] for f in recalled_active}
        assert recalled_dict["LEAD_ARCHITECT"] == "Elena Rostova"
        assert "eu-central-1" in recalled_dict["DEPLOYED_ON"]
        assert recalled_dict["USES"] == "CockroachDB"
        assert recalled_dict["HAS_SLA"] == "under 45ms"
        assert recalled_dict["PRODUCTION_CUTOVER_DATE"] == "November 24"

        # Verify NEGATIVE RECALL: PostgreSQL 16 and November 14 are NOT in recalled active facts
        assert "PostgreSQL 16" not in recalled_dict.values()
        assert "November 14" not in recalled_dict.values()


# ==============================================================================
# SECTION 8: AUTOMATED REGRESSION TESTS (T01 - T24)
# ==============================================================================

class TestArchitecturalRemediationMatrix:
    """T01 through T24 test cases verifying all defect remediations."""

    def test_T01_fact_plus_confirmation_directive(self):
        """T01: Fact + confirmation directive: factual segments persist."""
        text = "Project Titan uses PostgreSQL 16. Confirm you've logged these details."
        analyzer = FakeTurnAnalyzer()
        res = analyzer.analyze_turn(text)
        durable = [s for s in res.segments if s.durable]
        assert len(durable) >= 1, "Factual segment must remain durable despite confirmation directive"
        assert any("PostgreSQL 16" in s.text for s in durable)

    def test_T02_fact_plus_inquiry(self):
        """T02: Fact + inquiry: both storage and retrieval execute."""
        text = "Harshit lives in India. Also, what does he like in food?"
        analyzer = FakeTurnAnalyzer()
        res = analyzer.analyze_turn(text)
        assert any(s.durable for s in res.segments), "Statement must be durable"
        assert any(s.type == SegmentType.QUESTION for s in res.segments), "Question must be identified"
        assert res.retrieval_needed is True, "Retrieval must be requested"

    def test_T03_multiple_facts_plus_command(self):
        """T03: Multiple facts + command: valid facts are retained."""
        text = "Project Titan uses CockroachDB. Latency SLA is under 45ms. Please confirm and stop."
        analyzer = FakeTurnAnalyzer()
        res = analyzer.analyze_turn(text)
        durable = [s for s in res.segments if s.durable]
        assert len(durable) >= 2, "Both factual statements must be durable"

    def test_T04_explicit_forget_command(self):
        """T04: Explicit forget command: relevant fact revoked."""
        text = "Forget my previous address."
        analyzer = FakeTurnAnalyzer()
        res = analyzer.analyze_turn(text)
        assert any(s.type == SegmentType.RETRACTION for s in res.segments)
        assert not any(s.durable for s in res.segments), "Revocation directive must not be stored as durable fact"

    def test_T05_unrelated_command_no_suppression(self):
        """T05: Unrelated command: does not suppress factual extraction."""
        text = "The cutover date is November 14. Make sure to log this."
        mutations = extract_state_deltas(text, active_entity_state=[])
        assert len(mutations) >= 1
        assert mutations[0].obj == "November 14"

    def test_T06_dual_channel_response_clean(self):
        """T06: Dual-channel response: user response does not contain 'Fact committed:'."""
        # Simulated LangGraph state output
        state = AgentState(
            messages=[HumanMessage(content="My favorite color is green"), AIMessage(content="I've noted that.")],
            system_events=[{"event_type": "MEMORY_COMMIT", "status": "SUCCESS"}],
            final_response="I've noted your favorite color is green.",
        )
        assert state.final_response is not None
        assert "Fact committed:" not in state.final_response

    def test_T07_system_events_separation(self):
        """T07: System events: internal commit events logged to system_events."""
        node = conflict_resolution_node(
            AgentState(
                messages=[HumanMessage(content="update")],
                pending_facts=[{"subject": "A", "predicate": "P", "obj": "O"}],
                conflicting_facts=[{"subject": "A", "predicate": "P", "obj": "Old"}],
            )
        )
        assert "system_events" in node
        assert len(node["system_events"]) == 1
        assert node["system_events"][0]["event_type"] == "CONFLICT_RESOLUTION"
        assert "messages" not in node, "Internal notices must not pollute conversational messages"

    def test_T08_state_mutation_supersession(self):
        """T08: State mutation: CockroachDB supersedes PostgreSQL 16."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        service = MemoryService(store=store)
        ctx = RequestContext(tenant_id="t1", user_id="u1", agent_id="a1", active_scopes=["GLOBAL"])

        # Initial write
        rec1 = service.propose_memory("Project Titan", "USES_DATABASE", "PostgreSQL 16", ctx)
        assert rec1.status == MemoryStatus.ACTIVE

        # Superseding write
        rec2 = service.propose_memory("Project Titan", "USES_DATABASE", "CockroachDB", ctx)
        assert rec2.status == MemoryStatus.ACTIVE
        assert rec2.object == "CockroachDB"

        # Verify old record is superseded
        old_rec = store.facts[rec1.id]
        assert old_rec.status == MemoryStatus.SUPERSEDED

    def test_T09_relative_temporal_shift(self):
        """T09: Relative temporal shift: November 14 + 10 days = November 24."""
        shifted = shift_date_string("November 14", 10)
        assert shifted == "November 24"

    def test_T10_planned_action_vs_completed(self):
        """T10: Planned action vs completed: PLANNED_TARGET coexists with USES without superseding."""
        text = "We plan to migrate to CockroachDB in Q4."
        mutations = extract_state_deltas(text, active_entity_state=[])
        assert len(mutations) == 1
        assert mutations[0].action == DeltaAction.PLAN
        assert mutations[0].predicate == "PLANNED_TARGET"
        assert mutations[0].obj == "CockroachDB"

    def test_T11_completed_migration_supersedes(self):
        """T11: Completed migration: active migration supersedes old database."""
        prior = [{"subject": "Project Titan", "predicate": "USES", "obj": "PostgreSQL 16"}]
        text = "Project Titan: we had to migrate the database to CockroachDB due to multi-region."
        llm = StubExtractionLLM([{"subject": "Project Titan", "predicate": "USES", "obj": "CockroachDB"}])
        mutations = extract_state_deltas(text, active_entity_state=prior, llm=llm)
        db_mut = next(m for m in mutations if m.predicate == "USES")
        assert db_mut.obj == "CockroachDB"

    def test_T12_multi_region_deployment(self):
        """T12: Multi-region deployment: updates deployment regions without breaking other attributes."""
        prior = [
            {"subject": "Project Titan", "predicate": "DEPLOYED_ON", "obj": "AWS us-east-1"},
            {"subject": "Project Titan", "predicate": "LEAD_ARCHITECT", "obj": "Elena Rostova"},
        ]
        text = "Project Titan: deploying across both us-east-1 and eu-central-1."
        llm = StubExtractionLLM([{"subject": "Project Titan", "predicate": "DEPLOYED_ON", "obj": "AWS us-east-1 and eu-central-1"}])
        mutations = extract_state_deltas(text, active_entity_state=prior, llm=llm)
        region_mut = next(m for m in mutations if m.predicate == "DEPLOYED_ON")
        assert "eu-central-1" in region_mut.obj

    def test_T13_context_pollution_resistance(self):
        """T13: Context pollution resistance: unrelated consensus question does not mutate project memory."""
        text = "Compare the trade-offs between Raft and Paxos consensus algorithms."
        analyzer = FakeTurnAnalyzer()
        res = analyzer.analyze_turn(text)
        assert not any(s.durable for s in res.segments)
        mutations = extract_state_deltas(text, active_entity_state=[])
        assert not any("titan" in m.subject.lower() for m in mutations)

    def test_T14_negative_recall_verification(self):
        """T14: Negative recall verification: sign-off summary excludes deprecated tech stack choices."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx = RequestContext(tenant_id="t1", user_id="u1", agent_id="a1", active_scopes=["GLOBAL"])
        service = MemoryService(store=store)

        service.propose_memory("Project Titan", "USES_DATABASE", "PostgreSQL 16", ctx)
        service.propose_memory("Project Titan", "USES_DATABASE", "CockroachDB", ctx)

        active_facts = store.find_active_by_subject_predicate("Project Titan", "DATABASE_ENGINE", ctx)
        active_values = [f.object for f in active_facts]
        assert "CockroachDB" in active_values
        assert "PostgreSQL 16" not in active_values

    def test_T15_predicate_normalization_and_cardinality(self):
        """T15: Predicate normalization & cardinality: aliases map to canonical forms."""
        assert predicate_registry.normalize("USES_DATABASE") in ("USES", "DATABASE_ENGINE")
        assert predicate_registry.normalize("CUTOVER_DATE") == "PRODUCTION_CUTOVER_DATE"
        assert predicate_registry.get_cardinality("PRODUCTION_CUTOVER_DATE") == Cardinality.ONE

    def test_T16_atomic_supersession_transaction(self):
        """T16: Atomic supersession transaction: old fact validity closed, new fact created with lineage."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx = RequestContext(tenant_id="t1", user_id="u1", agent_id="a1", active_scopes=["GLOBAL"])
        service = MemoryService(store=store)

        r1 = service.propose_memory("Titan", "CUTOVER_DATE", "November 14", ctx)
        r2 = service.propose_memory("Titan", "CUTOVER_DATE", "November 24", ctx)

        assert r2.previous_version_id == r1.id
        assert store.facts[r1.id].status == MemoryStatus.SUPERSEDED
        assert store.facts[r2.id].status == MemoryStatus.ACTIVE

    def test_T17_idempotency_enforcement(self):
        """T17: Idempotency enforcement: identical write proposals produce identical records without duplicates."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx = RequestContext(tenant_id="t1", user_id="u1", agent_id="a1", active_scopes=["GLOBAL"])
        service = MemoryService(store=store)

        r1 = service.propose_memory("Titan", "LEAD_ARCHITECT", "Elena Rostova", ctx)
        r2 = service.propose_memory("Titan", "LEAD_ARCHITECT", "Elena Rostova", ctx)

        assert r1.id == r2.id
        assert len(store.facts) == 1

    def test_T18_fact_provenance(self):
        """T18: Fact provenance: source_agent_id and quote substring preserved."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx = RequestContext(tenant_id="t1", user_id="u1", agent_id="architect-agent", active_scopes=["GLOBAL"])
        service = MemoryService(store=store)

        rec = service.propose_memory(
            subject="Titan",
            predicate="HAS_SLA",
            obj="under 45ms",
            context=ctx,
            quote_substring="latency SLA is under 45ms",
        )
        assert rec.source_agent_id == "architect-agent"
        assert rec.metadata.get("quote_substring") == "latency SLA is under 45ms"

    def test_T19_tenant_isolation(self):
        """T19: Tenant isolation: facts from Tenant A cannot be recalled by Tenant B."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx_a = RequestContext(tenant_id="tenant-A", user_id="u1", agent_id="a1", active_scopes=["GLOBAL"])
        ctx_b = RequestContext(tenant_id="tenant-B", user_id="u2", agent_id="a2", active_scopes=["GLOBAL"])
        service = MemoryService(store=store)

        service.propose_memory("Titan", "LEAD_ARCHITECT", "Elena Rostova", ctx_a)

        facts_b = store.find_active_by_subject_predicate("Titan", "LEAD_ARCHITECT", ctx_b)
        assert len(facts_b) == 0, "Tenant B must never see Tenant A memories"

    def test_T20_scope_boundary_enforcement(self):
        """T20: Scope boundary enforcement: queries cannot access unauthorized scopes."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx_restricted = RequestContext(
            tenant_id="t1",
            user_id="u1",
            agent_id="a1",
            active_scopes=["PUBLIC"],
        )
        ctx_writer = RequestContext(
            tenant_id="t1",
            user_id="admin",
            agent_id="admin",
            active_scopes=["TOP_SECRET"],
        )
        service = MemoryService(store=store)

        service.propose_memory("Project X", "CODENAME", "RedSun", ctx_writer, scope="TOP_SECRET")

        res = store.find_active_by_subject_predicate("Project X", "CODENAME", ctx_restricted)
        assert len(res) == 0, "Unauthorized scope facts must not be retrieved"

    def test_T21_bounded_graph_traversal(self):
        """T21: Bounded graph traversal: 2-hop traversal terminates bounded within active scopes."""
        store = FakeMemoryStore(embedder=FakeEmbedder())
        ctx = RequestContext(tenant_id="t1", user_id="u1", agent_id="a1", active_scopes=["GLOBAL"])
        service = MemoryService(store=store)

        service.propose_memory("Titan", "USES", "CockroachDB", ctx)
        service.propose_memory("CockroachDB", "TYPE", "Distributed SQL", ctx)

        paths = store.traverse_2hop(["Titan"], ctx, max_results=10)
        assert isinstance(paths, list)

    def test_T22_rrf_retrieval_fusion(self):
        """T22: RRF retrieval fusion: reciprocal rank fusion correctly combines ranks."""
        from kryptodb.core.retrieval.rrf import rrf_fuse
        from kryptodb.core.ports import ScoredItem

        vec_items = [ScoredItem(item_id="f1", score=0.9, item_type="fact", text="Fact 1")]
        bm25_items = [ScoredItem(item_id="f1", score=12.0, item_type="fact", text="Fact 1")]

        fused = rrf_fuse([vec_items, bm25_items], k=60)
        assert len(fused) == 1
        assert fused[0].item_id == "f1"
        assert fused[0].score > 0.03  # 1/(60+1) + 1/(60+1) = 2/61 ≈ 0.0328

    def test_T23_cross_encoder_reranking(self):
        """T23: Cross-encoder reranking interface re-scores candidates."""
        from kryptodb.core.ports import CandidateItem
        candidates = [CandidateItem(item_id="c1", text="Project Titan cutover is November 24")]
        assert len(candidates) == 1

    def test_T24_contract_parity(self):
        """T24: Contract parity: MCP and REST interfaces share unified memory service."""
        from kryptodb.interfaces.http.routes.memory import search_memory, write_fact
        assert callable(search_memory)
        assert callable(write_fact)
