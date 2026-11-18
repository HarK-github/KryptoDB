"""Unit tests for Phase 5 Hybrid Search and Reciprocal Rank Fusion."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.retrieval.hybrid import HybridSearcher, reciprocal_rank_fusion
from kryptodb.core.ports import ScoredItem
from tests.fakes import FakeMemoryStore, FakeEmbedder


@pytest.fixture
def populated_store():
    embedder = FakeEmbedder(dimension=64)
    store = FakeMemoryStore(embedder=embedder)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default", "research"])

    facts = [
        MemoryRecord(
            id="f-alice",
            subject="Alice Smith",
            predicate="WORKS_FOR",
            object="Acme Corp",
            description="Alice Smith is employed by Acme Corp as lead researcher.",
            tenant_id="tenant-acme",
            scope="research",
            status=MemoryStatus.ACTIVE,
        ),
        MemoryRecord(
            id="f-bob",
            subject="Bob Jones",
            predicate="MANAGES",
            object="Database Cluster",
            description="Bob Jones manages the primary PostgreSQL database cluster.",
            tenant_id="tenant-acme",
            scope="default",
            status=MemoryStatus.ACTIVE,
        ),
        MemoryRecord(
            id="f-carol",
            subject="Carol White",
            predicate="AUTHORS",
            object="Quantum Paper",
            description="Carol White authored the publication on quantum computing.",
            tenant_id="tenant-acme",
            scope="default",
            status=MemoryStatus.ACTIVE,
        ),
        MemoryRecord(
            id="f-foreign",
            subject="Eve Black",
            predicate="OWNS",
            object="Secret Project",
            description="Eve Black owns the secret foreign project.",
            tenant_id="tenant-other",
            scope="default",
            status=MemoryStatus.ACTIVE,
        ),
    ]

    for f in facts:
        store.write_fact(f, ctx)

    return store, embedder


def test_rrf_mathematical_properties():
    # Two ranking lists
    list_a = [
        ScoredItem("doc-1", 1.0, "fact", "doc one"),
        ScoredItem("doc-2", 0.8, "fact", "doc two"),
    ]
    list_b = [
        ScoredItem("doc-2", 0.9, "fact", "doc two"),
        ScoredItem("doc-1", 0.5, "fact", "doc one"),
    ]
    # RRF with k=60
    fused = reciprocal_rank_fusion([list_a, list_b], k=60)
    # doc-1 rank 1 in a, rank 2 in b -> 1/61 + 1/62 = 0.016393 + 0.016129 = 0.032522
    # doc-2 rank 2 in a, rank 1 in b -> 1/62 + 1/61 = 0.032522 (tied)
    assert len(fused) == 2
    assert fused[0].score == pytest.approx(fused[1].score)


def test_exact_match_ranks_first(populated_store):
    store, embedder = populated_store
    searcher = HybridSearcher(store=store, embedder=embedder, rrf_k=60)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default", "research"])

    results = searcher.search_candidates("Alice Smith", ctx, top_k=5)
    assert len(results) > 0
    # Exact entity match for Alice Smith should be at the top
    top_result = results[0]
    assert "Alice Smith" in top_result.text or top_result.item_id.startswith("ent-")


def test_multi_word_fact_query_via_bm25(populated_store):
    store, embedder = populated_store
    searcher = HybridSearcher(store=store, embedder=embedder, rrf_k=60)
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default", "research"])

    results = searcher.search_candidates("quantum computing publication", ctx, top_k=5)
    assert len(results) > 0
    matched_ids = [r.item_id for r in results]
    assert "f-carol" in matched_ids


def test_tenant_isolation_returns_zero_foreign_facts(populated_store):
    store, embedder = populated_store
    searcher = HybridSearcher(store=store, embedder=embedder, rrf_k=60)
    # Query for Eve Black who belongs to tenant-other from tenant-acme context
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=["default"])

    results = searcher.search_candidates("Eve Black", ctx, top_k=5)
    for r in results:
        assert r.item_id != "f-foreign"
        assert "Eve Black" not in r.text


def test_inactive_scope_returns_zero_facts(populated_store):
    store, embedder = populated_store
    searcher = HybridSearcher(store=store, embedder=embedder, rrf_k=60)
    # Context with empty scopes
    ctx_empty = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-1", active_scopes=[])
    results = searcher.search_candidates("lead researcher", ctx_empty, top_k=5)
    assert len(results) == 0
