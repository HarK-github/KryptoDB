"""True 3-Way Multi-Hop GraphRAG Ablation Benchmark against live Neo4j + Plaintext vs Encrypted Parity.

Modes:
  1. flat_vector_only   : Vector index search only (BM25=off, 0-hop traversal, Cross-Encoder=off)
  2. hybrid_rrf_0hop    : Vector + BM25 RRF fusion (0-hop traversal, Cross-Encoder=off)
  3. full_pipeline_2hop : Hybrid RRF + 2-hop Cypher traversal + Cross-Encoder reranking

Run:
    KRYPTODB_ADMIN_API_KEY=... uv run python scripts/eval_ablations.py --storage-mode plaintext
    KRYPTODB_ADMIN_API_KEY=... KRYPTODB_STORAGE_MODE=encrypted KRYPTODB_KEY_FILE=/tmp/kdb_eval.key uv run python scripts/eval_ablations.py --storage-mode encrypted
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from fastapi import FastAPI

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.memory_service import memory_service
from kryptodb.core.retrieval.hybrid_search import hybrid_search_engine
from kryptodb.core.retrieval.ranking import AttributedMemoryResult, PathReranker
from kryptodb.core.retrieval.service import retrieval_service
from kryptodb.core.retrieval.traversal import multihop_traverser
from kryptodb.daemon.app import wire_runtime_services
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.schema import ensure_schema
from tests.eval.metrics import (
    compute_mrr as reciprocal_rank,
    compute_ndcg_at_k as ndcg_at_k,
    compute_precision_at_k as precision_at_k,
    compute_recall_at_k as recall_at_k,
)

HELD_OUT_FACTS = [
    ("Service Alpha", "DEPENDS_ON", "Cluster Borealis", "Service Alpha depends on Cluster Borealis for state"),
    ("Cluster Borealis", "HOSTED_IN", "eu-west-1", "Cluster Borealis is hosted in eu-west-1"),
    ("Cluster Borealis", "RUNS_ENGINE", "PostgreSQL 16", "Cluster Borealis runs PostgreSQL 16"),
    ("Service Beta", "DEPENDS_ON", "Queue Meridian", "Service Beta depends on Queue Meridian for async jobs"),
    ("Queue Meridian", "MAINTAINED_BY", "Priya Nair", "Queue Meridian is maintained by Priya Nair"),
    ("Priya Nair", "LOCATED_IN", "Bengaluru", "Priya Nair is located in Bengaluru"),
    ("Gateway Orion", "ROUTES_TO", "Service Alpha", "Gateway Orion routes external API traffic to Service Alpha"),
    ("Gateway Orion", "HAS_SLA", "99.95%", "Gateway Orion has a 99.95% availability SLA"),
    ("Pipeline Vega", "READS_FROM", "Cluster Borealis", "Pipeline Vega reads analytics snapshots from Cluster Borealis"),
    ("Pipeline Vega", "OWNED_BY", "Data Platform Team", "Pipeline Vega is owned by Data Platform Team"),
]

HELD_OUT_QUERIES = [
    {"query": "Which region hosts the database cluster that Service Alpha depends on?", "expected_terms": ["eu-west-1", "Cluster Borealis"]},
    {"query": "Who maintains the queue used by Service Beta?", "expected_terms": ["Priya Nair", "Queue Meridian"]},
    {"query": "What database engine powers the dependency of Service Alpha?", "expected_terms": ["PostgreSQL 16", "Cluster Borealis"]},
    {"query": "Where is the maintainer of Service Beta's queue located?", "expected_terms": ["Bengaluru", "Priya Nair"]},
    {"query": "Which team owns the analytics pipeline reading from Cluster Borealis?", "expected_terms": ["Data Platform Team", "Pipeline Vega"]},
]


def _score_hits(results: List[Any], expected_terms: List[str]) -> Dict[str, float]:
    retrieved_ids: List[str] = []
    relevant_ids: Set[str] = set()
    for idx, r in enumerate(results):
        summary = (getattr(r, "summary", "") or "").lower()
        fid = str(getattr(r, "fact_id", f"r-{idx}"))
        retrieved_ids.append(fid)
        if any(term.lower() in summary for term in expected_terms):
            relevant_ids.add(fid)
    while len(relevant_ids) < len(expected_terms):
        relevant_ids.add(f"__missed_{len(relevant_ids)}")
    return {
        "recall@10": min(1.0, recall_at_k(retrieved_ids, relevant_ids, 10)),
        "precision@10": min(1.0, precision_at_k(retrieved_ids, relevant_ids, 10)),
        "mrr": min(1.0, reciprocal_rank(retrieved_ids, relevant_ids)),
        "ndcg@10": min(1.0, ndcg_at_k(retrieved_ids, relevant_ids, 10)),
    }


def _run_mode(
    mode_name: str,
    query: str,
    ctx: RequestContext,
    scope: str,
    storage_mode: str,
    app: FastAPI,
) -> List[AttributedMemoryResult]:
    """Execute one of the three genuinely distinct retrieval modes."""
    if mode_name == "flat_vector_only":
        if storage_mode == "encrypted":
            enc_ret = app.state.encrypted_retrieval
            q_vec = enc_ret.embed_fn(query)
            vec_hits = enc_ret.vector_index.search(scope, q_vec, top_k=10)
            results: List[AttributedMemoryResult] = []
            for fid, score in vec_hits[:10]:
                rec = enc_ret.store.get_fact(fid, ctx)
                summary = rec.description or f"{rec.subject} {rec.predicate} {rec.object}" if rec else fid
                results.append(
                    AttributedMemoryResult(
                        fact_id=str(fid),
                        summary=summary,
                        hops=0,
                        final_score=round(float(score), 4),
                        relevance_score=round(float(score), 4),
                        confidence=rec.confidence if rec else 1.0,
                        recency_score=1.0,
                        citation_tag=f"[{ctx.tenant_id}:{scope}]",
                        provenance_path=[],
                    )
                )
            return results
        # Plaintext flat vector-only: vector seed search, BM25=off, 0-hop traversal, rerank=off
        seeds = hybrid_search_engine.find_semantic_fallback_seeds(
            query=query, tenant_id=ctx.tenant_id, active_scopes=[scope], top_k=10
        )
        return [
            AttributedMemoryResult(
                fact_id=str(s.get("id", f"ent-{i}")),
                summary=str(s.get("name", "")),
                hops=0,
                final_score=round(float(s.get("score", 0.0)), 4),
                relevance_score=round(float(s.get("score", 0.0)), 4),
                confidence=1.0,
                recency_score=1.0,
                citation_tag=f"[{ctx.tenant_id}:{scope}]",
                provenance_path=[],
            )
            for i, s in enumerate(seeds[:10])
        ]

    if mode_name == "hybrid_rrf_0hop":
        if storage_mode == "encrypted":
            return retrieval_service.search(
                query=query, tenant_id=ctx.tenant_id, active_scopes=[scope], top_k=10, max_hops=0, now=ctx.now
            )
        seeds = hybrid_search_engine.find_seed_entities(
            query=query, tenant_id=ctx.tenant_id, active_scopes=[scope], top_k=10
        )
        return [
            AttributedMemoryResult(
                fact_id=str(s.get("id", f"ent-{i}")),
                summary=str(s.get("name", "")),
                hops=0,
                final_score=round(float(s.get("score", 0.0)), 4),
                relevance_score=round(float(s.get("score", 0.0)), 4),
                confidence=1.0,
                recency_score=1.0,
                citation_tag=f"[{ctx.tenant_id}:{scope}]",
                provenance_path=[],
            )
            for i, s in enumerate(seeds[:10])
        ]

    # full_pipeline_2hop: Hybrid RRF + 2-hop Cypher traversal + Cross-Encoder reranking
    return retrieval_service.search(
        query=query, tenant_id=ctx.tenant_id, active_scopes=[scope], top_k=10, max_hops=2, now=ctx.now
    )


def run_ablation(storage_mode: str = "plaintext") -> Dict[str, Any]:
    try:
        if hasattr(neo4j_client, "connect") and not getattr(neo4j_client, "driver", None):
            neo4j_client.connect()
    except Exception as exc:
        return {
            "status": "BLOCKED",
            "label": "[BLOCKED]",
            "reason": f"Live Neo4j instance unavailable ({exc}); cannot run live ablation.",
            "storage_mode": storage_mode,
        }
    if not getattr(neo4j_client, "driver", None) or (
        hasattr(neo4j_client, "check_health") and not neo4j_client.check_health()
    ):
        return {
            "status": "BLOCKED",
            "label": "[BLOCKED]",
            "reason": "Live Neo4j instance unavailable; cannot run live ablation.",
            "storage_mode": storage_mode,
        }

    ensure_schema(neo4j_client.driver)
    app = FastAPI()
    wire_runtime_services(app, storage_mode)

    scope = f"ablation_{storage_mode}"
    ctx = RequestContext.create(tenant_id="ablation-tenant", agent_id="ablation-runner", active_scopes=[scope])

    for s, p, o, desc in HELD_OUT_FACTS:
        memory_service.propose_memory(subject=s, predicate=p, obj=o, description=desc, scope=scope, context=ctx)

    modes = ["flat_vector_only", "hybrid_rrf_0hop", "full_pipeline_2hop"]
    report: Dict[str, Any] = {"status": "OK", "label": "[LIVE]", "storage_mode": storage_mode, "modes": {}}

    for mode_name in modes:
        latencies: List[float] = []
        recalls, precisions, mrrs, ndcgs = [], [], [], []
        for item in HELD_OUT_QUERIES:
            t0 = time.perf_counter()
            hits = _run_mode(mode_name, item["query"], ctx, scope, storage_mode, app)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            scores = _score_hits(hits, item["expected_terms"])
            recalls.append(scores["recall@10"])
            precisions.append(scores["precision@10"])
            mrrs.append(scores["mrr"])
            ndcgs.append(scores["ndcg@10"])

        sorted_lat = sorted(latencies)
        p50 = statistics.median(sorted_lat)
        p95 = sorted_lat[min(len(sorted_lat) - 1, int(0.95 * len(sorted_lat)))]
        report["modes"][mode_name] = {
            "mean_recall@10": round(statistics.mean(recalls), 4),
            "mean_precision@10": round(statistics.mean(precisions), 4),
            "mean_mrr": round(statistics.mean(mrrs), 4),
            "mean_ndcg@10": round(statistics.mean(ndcgs), 4),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="KryptoDB 3-Way Multi-Hop Ablation Benchmark")
    parser.add_argument("--storage-mode", choices=["plaintext", "encrypted"], default="plaintext")
    args = parser.parse_args()
    result = run_ablation(storage_mode=args.storage_mode)
    print(json.dumps(result, indent=2))
    if result.get("status") != "OK":
        sys.exit(2)


if __name__ == "__main__":
    main()
