from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from kryptodb.core.filters import FilterBuilder
from kryptodb.core.retrieval.hybrid_search import reciprocal_rank_fusion, HybridSearchEngine
from kryptodb.core.retrieval.traversal import MultiHopTraverser
from kryptodb.core.retrieval.ranking import PathReranker, AttributedMemoryResult
from kryptodb.core.retrieval.service import RetrievalService

def test_reciprocal_rank_fusion():
    vector_results = [
        {"id": "doc1", "name": "Alice"},
        {"id": "doc2", "name": "Bob"},
        {"id": "doc3", "name": "Charlie"}
    ]
    bm25_results = [
        {"id": "doc2", "name": "Bob"},
        {"id": "doc1", "name": "Alice"},
        {"id": "doc4", "name": "Diana"}
    ]
    
    fused = reciprocal_rank_fusion(vector_results, bm25_results, k=60)
    assert len(fused) == 4
    
    # doc1 and doc2 appear in both lists, so they must rank higher than doc3 and doc4
    top_ids = [item["id"] for item in fused[:2]]
    assert "doc1" in top_ids
    assert "doc2" in top_ids
    assert fused[0]["rrf_score"] > fused[2]["rrf_score"]

def test_filter_builder():
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    clause, params = FilterBuilder.build_edge_filter(
        tenant_id="tenant-alpha",
        active_scopes=["ORG:eng", "PROJECT:x"],
        status="active",
        now=now,
        edge_alias="r"
    )
    assert "r.tenant_id = $tenant_id" in clause
    assert "r.status = $status" in clause
    assert "r.scope IN $scopes" in clause
    assert "r.valid_until IS NULL OR r.valid_until > $now_iso" in clause
    assert "r.valid_from IS NULL OR r.valid_from <= $now_iso" in clause
    assert params["tenant_id"] == "tenant-alpha"
    assert set(params["scopes"]) == {"ORG:eng", "PROJECT:x"}

def test_multihop_traverser_stratification_and_cycle_control():
    mock_client = MagicMock()
    # Mock return for 1-hop and 2-hop
    mock_client.execute_query_with_retry.side_effect = [
        # 1-hop return
        ([
            {
                "s_id": "n1", "s_name": "Alice",
                "t_id": "n2", "t_name": "Bob",
                "edge_id": "e1", "predicate": "MANAGES",
                "description": "Alice manages Bob", "confidence": 1.0,
                "trust_weight": 1.0, "scope": "ORG",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ], None, None),
        # 2-hop return
        ([
            {
                "s_id": "n1", "s_name": "Alice",
                "mid_id": "n2", "mid_name": "Bob",
                "t_id": "n3", "t_name": "Database X",
                "e1_id": "e1", "e1_predicate": "MANAGES", "e1_desc": "Alice manages Bob",
                "e1_conf": 1.0, "e1_scope": "ORG",
                "e2_id": "e2", "e2_predicate": "ACCESSES", "e2_desc": "Bob accesses Database X",
                "e2_conf": 0.9, "e2_scope": "PROJECT",
                "e2_created_at": datetime.now(timezone.utc).isoformat()
            }
        ], None, None)
    ]

    traverser = MultiHopTraverser(client=mock_client)
    paths = traverser.traverse_from_seeds(
        seed_entity_ids=["n1"],
        tenant_id="tenant-alpha",
        max_1hop=5,
        max_2hop=5
    )

    assert len(paths) == 2
    assert paths[0]["hops"] == 1
    assert paths[0]["summary"] == "Alice MANAGES Bob"
    assert paths[1]["hops"] == 2
    assert "Database X" in paths[1]["summary"]
    # Check that cycle bounce-backs are prevented in cypher query
    calls = mock_client.execute_query_with_retry.call_args_list
    cypher_2hop = calls[1][0][0]
    assert "s.id <> t.id AND s.id <> mid.id AND mid.id <> t.id" in cypher_2hop

def test_path_reranker_composite_score():
    reranker = PathReranker()
    now = datetime.now(timezone.utc)
    candidate_paths = [
        {
            "hops": 1,
            "summary": "Alice MANAGES Bob in engineering",
            "confidence": 1.0,
            "created_at": now.isoformat(),
            "path_edges": [{
                "hop": 1, "source": "Alice", "predicate": "MANAGES",
                "target": "Bob", "edge_id": "edge-1", "scope": "ORG", "confidence": 1.0
            }]
        },
        {
            "hops": 2,
            "summary": "Bob ACCESSES Database Cluster X for customer records",
            "confidence": 0.9,
            "created_at": (now - timedelta(days=10)).isoformat(),
            "path_edges": [
                {"hop": 1, "source": "Alice", "predicate": "MANAGES", "target": "Bob", "edge_id": "edge-1", "scope": "ORG", "confidence": 1.0},
                {"hop": 2, "source": "Bob", "predicate": "ACCESSES", "target": "Database Cluster X", "edge_id": "edge-2", "scope": "PROJECT", "confidence": 0.9}
            ]
        }
    ]

    results = reranker.rank_paths(
        query="Who manages Bob?",
        paths=candidate_paths,
        now=now
    )

    assert len(results) == 2
    assert results[0].summary == "Alice MANAGES Bob in engineering"
    assert results[0].final_score > results[1].final_score
    assert "[Scope: ORG, Edge: edge-1]" in results[0].citation_tag
    assert len(results[0].provenance_path) == 1
    assert len(results[1].provenance_path) == 2
