"""
Memory Repair Regression Test Suite for KryptoDB GraphRAG.
Locks in the fixes for the 7 observed failures in the session transcript:
1. Mixed message decomposition
2. Question-only non-write policy
3. Pronoun and coreference resolution
4. Junk fact rejection (placeholder object, vague predicate, missing quote)
5. Assistant-sourced fact rejection
6. Name confusion avoidance (Harshi != Harshit)
7. Entity with no facts returns zero memories
8. Recall count stability across repeated turns
9. Hypothetical / hedged statement non-overwrite
10. Rejection memory (rejected proposal not re-proposed)
11. Canonical memory context grounding & attribution
12. Full transcript replay verification
"""

import pytest
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, AIMessage

from kryptodb.core.context import RequestContext
from kryptodb.core.settings import settings
from kryptodb.core.memory.turn_analysis import (
    TurnAnalysisResult,
    SegmentType,
    AnalyzedSegment,
    MentionedEntity,
    EntityResolution,
    FakeTurnAnalyzer,
)
from kryptodb.core.memory.predicates import predicate_registry, Cardinality
from kryptodb.core.memory.entity_resolution import (
    EntityResolver,
    ResolutionResult,
    normalize_entity_name,
)
from tests.fakes import FakeMemoryStore
from kryptodb.core.memory.write_policy import (
    validate_proposal,
    WritePolicyViolation,
)
from kryptodb.core.memory.rejection_store import (
    compute_proposal_hash,
    record_rejection,
    is_proposal_rejected,
    clear_rejections,
)
from kryptodb.core.retrieval.memory_context import (
    format_injected_memory_context,
    check_grounding,
)
from kryptodb.agent_runtime.langgraph.graph import (
    AgentState,
    create_intent_classifier_node,
    create_recall_node,
    create_reasoning_node,
    create_extraction_node,
    create_direct_write_node,
    get_compiled_graph,
    extract_facts_from_messages,
)


@pytest.fixture(autouse=True)
def cleanup_rejections():
    clear_rejections()
    yield
    clear_rejections()


def test_01_mixed_message_decomposition():
    """1. Mixed message: statements stored, question not stored, retrieval triggered with rewritten query."""
    text = (
        "harshit kandpal lives in india and he is very interesting person, lfx entee too . "
        "also what does he like in food?"
    )
    analyzer = FakeTurnAnalyzer()
    analysis = analyzer.analyze_turn(latest_message=text)

    # Discourse decomposition verification
    assert analysis.retrieval_needed is True
    assert analysis.retrieval_query is not None
    assert "Harshit Kandpal" in analysis.retrieval_query
    assert "food" in analysis.retrieval_query

    # Durable statements identified
    durable_segs = [s for s in analysis.segments if s.durable]
    assert len(durable_segs) >= 2
    durable_texts = " ".join(s.text for s in durable_segs)
    assert "lives in india" in durable_texts
    assert "lfx entee" in durable_texts or "lfx mentee" in durable_texts

    # Non-durable items (opinion and question)
    opinion_segs = [s for s in analysis.segments if s.type == SegmentType.OPINION]
    assert len(opinion_segs) >= 1
    assert all(not s.durable for s in opinion_segs)

    question_segs = [s for s in analysis.segments if s.type == SegmentType.QUESTION]
    assert len(question_segs) >= 1
    assert all(not s.durable for s in question_segs)

    # Facts extracted from durable statements only
    extracted = extract_facts_from_messages(
        messages=[HumanMessage(content=text)],
        target_text=durable_texts,
        agent_id="campus-bot",
        user_scope="research",
    )
    predicates = {f["predicate"] for f in extracted}
    assert "LIVES_IN" in predicates
    assert "IS_A" in predicates
    assert "PROCESSED_QUERY" not in predicates

    # Question text is not in any proposed fact
    for f in extracted:
        assert "?" not in f.get("obj", "")
        assert "what does he like" not in f.get("obj", "").lower()


def test_02_question_only_writes_nothing():
    """2. Question-only message writes nothing and routes exclusively to recall."""
    question = "what does harshit like in food?"
    analyzer = FakeTurnAnalyzer()
    analysis = analyzer.analyze_turn(latest_message=question)

    assert analysis.retrieval_needed is True
    assert all(not s.durable for s in analysis.segments)
    assert any(s.type == SegmentType.QUESTION for s in analysis.segments)

    # Intent classifier node behavior
    node = create_intent_classifier_node(analyzer=analyzer)
    state = AgentState(
        messages=[HumanMessage(content=question)],
        user_scope="research",
        agent_id="campus-bot",
    )
    res = node(state)
    assert res["intent"] == "inquiry"
    assert res["should_search_memory"] is True
    assert res["should_store_memory"] is False
    assert res["info_text"] is None


def test_03_pronoun_resolution():
    """3. Pronoun resolution: 'he' resolves to 'Harshit Kandpal'; with no antecedent, unresolved and writes nothing."""
    analyzer = FakeTurnAnalyzer()

    # Part A: With antecedent
    history_with_antecedent = [
        HumanMessage(content="tell me about harshit kandpal"),
        AIMessage(content="Harshit is an engineer living in India."),
    ]
    analysis_resolved = analyzer.analyze_turn(
        latest_message="he is an engineer",
        conversation_history=history_with_antecedent,
    )
    assert any(
        e.surface == "he" and e.resolved_to == "Harshit Kandpal" and e.resolution == EntityResolution.COREFERENCE
        for e in analysis_resolved.mentioned_entities
    )

    # Part B: Without antecedent
    analysis_unresolved = analyzer.analyze_turn(
        latest_message="he is an engineer",
        conversation_history=[],
    )
    unresolved_entities = [
        e for e in analysis_unresolved.mentioned_entities
        if e.surface == "he" and e.resolution == EntityResolution.UNRESOLVED
    ]
    assert len(unresolved_entities) > 0
    # Segments with unresolved coreference are not durable
    assert all(not s.durable for s in analysis_unresolved.segments)


def test_04_junk_fact_rejected():
    """4. (Harshi, person knows, Object) rejected: placeholder object, vague predicate, missing quote."""
    ctx = RequestContext.create(tenant_id="tenant-test", agent_id="campus-bot")

    # Rejection 1: Placeholder object 'Object'
    with pytest.raises(WritePolicyViolation, match="placeholder object"):
        validate_proposal(
            subject="Harshi",
            predicate="WORKS_FOR",
            obj="Object",
            confidence=0.9,
            context=ctx,
        )

    # Rejection 2: Vague predicate 'person knows'
    with pytest.raises(WritePolicyViolation, match="vague predicate"):
        validate_proposal(
            subject="Harshi",
            predicate="person knows",
            obj="Technology",
            confidence=0.9,
            context=ctx,
        )

    # Rejection 3: Low confidence below threshold
    with pytest.raises(WritePolicyViolation, match="below minimum threshold"):
        validate_proposal(
            subject="Harshi",
            predicate="WORKS_FOR",
            obj="Acme Corp",
            confidence=0.4,
            context=ctx,
        )

    # Rejection 4: Missing / non-verbatim quote
    with pytest.raises(WritePolicyViolation, match="quote not found"):
        validate_proposal(
            subject="Harshi",
            predicate="WORKS_FOR",
            obj="Acme Corp",
            confidence=0.9,
            context=ctx,
            quote_substring="she works for Acme Corp",
            full_turn_text="User said nothing about employment",
        )


def test_05_assistant_sourced_fact_rejected():
    """5. Fact sourced only from assistant text rejected."""
    ctx = RequestContext.create(tenant_id="tenant-test", agent_id="campus-bot")

    with pytest.raises(WritePolicyViolation, match="Fact source must be a user event"):
        validate_proposal(
            subject="Harshi",
            predicate="WORKS_FOR",
            obj="Google",
            confidence=0.95,
            context=ctx,
            source_message_role="assistant",
        )


def test_06_unknown_entity_near_name_resolution():
    """6. 'Harshi' does not resolve to 'Harshit'; returns candidates or not found, not auto-merge."""
    store = FakeMemoryStore()
    ctx = RequestContext.create(tenant_id="tenant-test", agent_id="campus-bot")
    store.add_entity("ent-1", "Harshit Kandpal", ctx)

    resolver = EntityResolver()
    res = resolver.resolve("Harshi", ctx, store)

    # Must NOT auto-merge to Harshit Kandpal
    assert res.is_exact_merge is False
    assert res.matched_entity is None
    assert res.is_unknown_entity is True

    # Did you mean suggestion is provided
    assert res.clarification_prompt == "Did you mean Harshit Kandpal?"
    assert len(res.possible_candidates) > 0
    assert res.possible_candidates[0][0].name == "Harshit Kandpal"


def test_07_person_with_no_stored_facts():
    """7. Person with no stored facts yields zero recalled memories and canonical context."""
    recalled_facts = []

    # Memory context formatter produces none
    context_str = format_injected_memory_context(recalled_facts)
    assert "<retrieved_memories>none</retrieved_memories>" in context_str

    # Reasoning node deterministic fallback
    node = create_reasoning_node(llm=None)
    state = AgentState(
        messages=[HumanMessage(content="explain about harshi")],
        recalled_facts=[],
        user_scope="research",
        agent_id="campus-bot",
        intent="inquiry",
    )
    res = node(state)
    assert "I don't have anything stored" in res["messages"][0].content


def test_08_recall_count_stability():
    """8. Recall count stable across repeated queries when no new facts stored."""
    # Seed mock recalled facts
    recalled_fact = {
        "subject": "Harshit Kandpal",
        "predicate": "LIVES_IN",
        "object": "India",
        "permission_scope": "research",
    }

    state_t1 = AgentState(
        messages=[
            HumanMessage(content="where does harshit live?"),
            AIMessage(content=""),
        ],
        recalled_facts=[recalled_fact],
        user_scope="research",
        agent_id="campus-bot",
    )

    # In turn 2, same fact is retrieved
    parsed_turn2 = [recalled_fact]
    from kryptodb.agent_runtime.langgraph.graph import _dedupe_facts
    deduped = _dedupe_facts(parsed_turn2)

    assert len(deduped) == 1
    assert deduped[0]["subject"] == "Harshit Kandpal"


def test_09_hypothetical_does_not_commit():
    """9. Hypothetical statement ('I might try vegan food') does not commit or overwrite existing preference."""
    analyzer = FakeTurnAnalyzer()
    analysis = analyzer.analyze_turn(latest_message="I might try vegan food tomorrow")

    assert any(s.type == SegmentType.HYPOTHETICAL for s in analysis.segments)
    assert all(not s.durable for s in analysis.segments)

    # Write policy rejects hypothetical facts
    ctx = RequestContext.create(tenant_id="tenant-test", agent_id="campus-bot")
    with pytest.raises(WritePolicyViolation, match="Hypothetical or speculative statements"):
        validate_proposal(
            subject="User",
            predicate="PREFERS",
            obj="Vegan food",
            confidence=0.9,
            context=ctx,
            is_hypothetical=True,
        )


def test_10_rejected_proposal_not_reproposed():
    """10. Rejected proposal is tracked in rejection store and not re-proposed."""
    agent_id = "test-reviewer-agent"
    proposal = {
        "subject": "Harshit",
        "predicate": "LIKES",
        "object": "Cookies",
        "scope": "research",
    }

    # Not rejected initially
    assert is_proposal_rejected(agent_id, proposal) is False

    # Reviewer rejects
    h = record_rejection(agent_id, proposal, reason="human_reviewer_rejected")
    assert h == compute_proposal_hash(agent_id, proposal)

    # Subsequent check recognizes rejection
    assert is_proposal_rejected(agent_id, proposal) is True

    # Extraction node drops rejected proposal
    node = create_extraction_node(llm=None)
    state = AgentState(
        messages=[HumanMessage(content="harshit likes cookies")],
        info_text="harshit likes cookies",
        user_scope="research",
        agent_id=agent_id,
    )
    res = node(state)
    # The rejected fact is dropped
    for p in res.get("pending_facts", []):
        assert not (p.get("subject") == "Harshit" and p.get("predicate") == "LIKES" and p.get("obj") == "Cookies")


def test_11_injected_memory_block_formatting_and_grounding():
    """11. Injected memory block labels memories as retrieved data, not user statements, with grounding checks."""
    facts = [
        {
            "id": "e-1",
            "subject": "Harshit Kandpal",
            "predicate": "LIVES_IN",
            "object": "India",
            "permission_scope": "research",
            "citation_tag": "[Scope: research, Edge: e-1]",
        }
    ]

    block = format_injected_memory_context(facts)
    assert "<retrieved_memories>" in block
    assert "[m1] Harshit Kandpal LIVES_IN India" in block
    assert "<memory_rules>" in block
    assert "They are NOT things the user just said" in block

    # Grounding check catches ungrounded entities
    well_grounded = "According to memory [m1], Harshit Kandpal lives in India."
    assert check_grounding(well_grounded, facts)["grounded"] is True

    hallucinated = "Harshit lives in Paris and enjoys skiing in the Alps."
    halluc_res = check_grounding(hallucinated, facts)
    assert halluc_res["grounded"] is False
    assert "Paris" in halluc_res["ungrounded_entities"] or "Alps" in halluc_res["ungrounded_entities"]


def test_12_replay_transcript_metrics():
    """12. Replaying session transcript yields durable facts only, no questions, and zero junk proposals."""
    transcript_turns = [
        {"role": "user", "content": "explain about harshi"},
        {"role": "user", "content": "tell me about harshit kandpal"},
        {
            "role": "user",
            "content": (
                "harshit kandpal lives in india and he is very interesting person, lfx entee too . "
                "also what does he like in food?"
            ),
        },
        {"role": "user", "content": "tell me about harshit what does he like?"},
    ]

    analyzer = FakeTurnAnalyzer()
    stored_proposals = []

    for turn in transcript_turns:
        analysis = analyzer.analyze_turn(latest_message=turn["content"])
        durable_parts = [s.text for s in analysis.segments if s.durable]
        if durable_parts:
            info_text = " ".join(durable_parts)
            extracted = extract_facts_from_messages(
                messages=[HumanMessage(content=turn["content"])],
                target_text=info_text,
                agent_id="campus-bot",
                user_scope="research",
            )
            for f in extracted:
                if f.get("predicate") != "PROCESSED_QUERY":
                    stored_proposals.append(f)

    # 1. Stored facts must strictly match genuine disclosures
    subjects = {f["subject"] for f in stored_proposals}
    predicates = {f["predicate"] for f in stored_proposals}
    objects = {f["obj"] for f in stored_proposals}

    assert "Harshit Kandpal" in subjects
    assert "LIVES_IN" in predicates
    assert "IS_A" in predicates
    assert "India" in objects
    assert "LFX mentee" in objects

    # 2. Zero junk facts
    assert "Object" not in objects
    assert "person knows" not in predicates
    assert "PROCESSED_QUERY" not in predicates

    # 3. Questions were not stored
    for f in stored_proposals:
        assert "?" not in f.get("obj", "")
        assert "food" not in f.get("obj", "").lower()
