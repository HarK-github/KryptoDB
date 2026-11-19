"""Unit tests for Reciprocal Rank Fusion (RRF) and hybrid search ranking."""

from __future__ import annotations

import pytest

from kryptodb.core.ports import ScoredItem
from kryptodb.core.retrieval.rrf import reciprocal_rank_fusion, rrf_fuse


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


def test_rrf_with_dicts():
    vec_results = [
        {"id": "doc-1", "name": "Document 1", "score": 0.95},
        {"id": "doc-2", "name": "Document 2", "score": 0.80},
    ]
    bm25_results = [
        {"id": "doc-2", "name": "Document 2", "score": 12.5},
        {"id": "doc-3", "name": "Document 3", "score": 8.0},
    ]
    fused = reciprocal_rank_fusion(vec_results, bm25_results, k=60)
    assert len(fused) == 3
    # doc-2 is in both streams (rank 2 in vec, rank 1 in bm25) -> highest score
    assert fused[0]["id"] == "doc-2"
    assert "rrf_score" in fused[0]
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]


def test_rrf_fuse_top_k():
    list_a = [ScoredItem(f"doc-{i}", 1.0 / (i + 1), "fact", f"doc {i}") for i in range(20)]
    list_b = [ScoredItem(f"doc-{i}", 1.0 / (i + 1), "fact", f"doc {i}") for i in range(10, 30)]

    fused = rrf_fuse([list_a, list_b], k=60, top_k=5)
    assert len(fused) == 5
    assert fused[0].item_id == "doc-0" or fused[0].item_id == "doc-10"


def test_rrf_empty_streams():
    assert reciprocal_rank_fusion([], k=60) == []
    assert reciprocal_rank_fusion([], [], k=60) == []
    assert rrf_fuse([], k=60, top_k=5) == []


def test_rrf_single_stream_preserves_order():
    items = [
        ScoredItem("doc-1", 1.0, "fact", "doc one"),
        ScoredItem("doc-2", 0.5, "fact", "doc two"),
        ScoredItem("doc-3", 0.2, "fact", "doc three"),
    ]
    fused = reciprocal_rank_fusion(items, k=60)
    assert [x.item_id for x in fused] == ["doc-1", "doc-2", "doc-3"]
