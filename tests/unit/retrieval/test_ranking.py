"""Unit tests for Phase 6: Ranking pipeline, scale harmony, and provenance attribution."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from kryptodb.core.retrieval.ranking import PathReranker, AttributedMemoryResult
from kryptodb.core.retrieval.provenance import format_citation, format_provenance_chain
from kryptodb.core.retrieval.ranking import ProvenanceStep


def test_ranking_scale_harmony():
    """Verify that composite scoring stays strictly bounded in [0, 1] without double penalization."""
    reranker = PathReranker()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    paths = [
        {
            "hops": 1,
            "summary": "Alice WORKS_FOR Acme Corp",
            "confidence": 1.0,
            "created_at": now.isoformat(),
            "path_edges": [{
                "hop": 1, "source": "Alice", "predicate": "WORKS_FOR", "target": "Acme Corp",
                "edge_id": "e1", "scope": "default", "confidence": 1.0
            }]
        },
        {
            "hops": 2,
            "summary": "Alice WORKS_FOR Acme Corp -> Acme Corp LOCATED_IN New York",
            "confidence": 0.9,
            "created_at": (now - timedelta(days=10)).isoformat(),
            "path_edges": [
                {"hop": 1, "source": "Alice", "predicate": "WORKS_FOR", "target": "Acme Corp", "edge_id": "e1", "scope": "default", "confidence": 1.0},
                {"hop": 2, "source": "Acme Corp", "predicate": "LOCATED_IN", "target": "New York", "edge_id": "e2", "scope": "default", "confidence": 0.8},
            ]
        }
    ]

    results = reranker.rank_paths(query="Where is Alice located?", paths=paths, now=now)
    assert len(results) == 2
    for r in results:
        assert 0.0 <= r.final_score <= 1.0
        assert 0.0 <= r.relevance_score <= 1.0
        assert 0.0 <= r.confidence <= 1.0
        assert 0.0 <= r.recency_score <= 1.0


def test_high_relevance_2hop_outranks_low_relevance_1hop():
    """A highly relevant 2-hop answer MUST outrank a mediocre 1-hop fact."""
    reranker = PathReranker()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    paths = [
        {
            "hops": 1,
            "summary": "Alice OWNS blue car",
            "confidence": 1.0,
            "created_at": now.isoformat(),
            "path_edges": [{
                "hop": 1, "source": "Alice", "predicate": "OWNS", "target": "blue car",
                "edge_id": "e-car", "scope": "default", "confidence": 1.0
            }]
        },
        {
            "hops": 2,
            "summary": "Alice MANAGES Alpha Team -> Alpha Team DEPLOYS Quantum Neural Network",
            "confidence": 1.0,
            "created_at": now.isoformat(),
            "path_edges": [
                {"hop": 1, "source": "Alice", "predicate": "MANAGES", "target": "Alpha Team", "edge_id": "e-team", "scope": "default", "confidence": 1.0},
                {"hop": 2, "source": "Alpha Team", "predicate": "DEPLOYS", "target": "Quantum Neural Network", "edge_id": "e-quantum", "scope": "default", "confidence": 1.0},
            ]
        }
    ]

    # Query specifically seeking quantum neural network deployment
    results = reranker.rank_paths(query="quantum neural network deployment by Alice", paths=paths, now=now)
    assert len(results) == 2
    assert results[0].hops == 2
    assert "Quantum Neural Network" in results[0].summary


def test_provenance_and_citation_formatting():
    """Verify that returned memory results carry complete edge citation and chain."""
    steps = [
        ProvenanceStep(hop=1, source="Alice", predicate="WORKS_FOR", target="Acme Corp", edge_id="e1", scope="research"),
        ProvenanceStep(hop=2, source="Acme Corp", predicate="LOCATED_IN", target="Zurich", edge_id="e2", scope="research"),
    ]
    citation = format_citation(tenant_id="tenant-alpha", scopes=["research"], edge_ids=["e1", "e2"])
    assert citation == "[tenant:tenant-alpha, scope:research, edges:e1->e2]"

    chain = format_provenance_chain(steps)
    assert chain == "(Alice)-[WORKS_FOR]->(Acme Corp)-[LOCATED_IN]->(Zurich)"
