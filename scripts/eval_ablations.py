"""Multi-hop GraphRAG ablation benchmark against live Neo4j (Vector-Only vs Hybrid vs Full 2-Hop + Rerank).

Also supports --storage-mode encrypted to measure Recall@10 and latency parity with v3 encryption enabled.
Run:
    KRYPTODB_ADMIN_API_KEY=... uv run python scripts/eval_ablations.py
    KRYPTODB_STORAGE_MODE=encrypted KRYPTODB_KEY_FILE=/tmp/kdb.key uv run python scripts/eval_ablations.py --storage-mode encrypted
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any, Dict, List, Set

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.memory_service import memory_service
from kryptodb.core.retrieval.service import retrieval_service
from tests.eval.metrics import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank

# Held-out multi-hop evaluation corpus (2-hop chains + distractors)
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
    # If expected terms were missed, ensure denominator reflects expected count
    while len(relevant_ids) < len(expected_terms):
        relevant_ids.add(f"__missed_{len(relevant_ids)}")
    return {
        "recall@10": recall_at_k(retrieved_ids, relevant_ids, 10),
        "precision@10": precision_at_k(retrieved_ids, relevant_ids, 10),
        "mrr": reciprocal_rank(retrieved_ids, relevant_ids),
        "ndcg@10": ndcg_at_k(retrieved_ids, relevant_ids, 10),
    }


def run_ablation(storage_mode: str = "plaintext") -> Dict[str, Any]:
    scope = f"ablation_{storage_mode}"
    ctx = RequestContext.create(tenant_id="ablation-tenant", agent_id="ablation-runner", active_scopes=[scope])

    for s, p, o, desc in HELD_OUT_FACTS:
        memory_service.propose_memory(subject=s, predicate=p, obj=o, description=desc, scope=scope, context=ctx)

    modes = [
        ("vector_only_0hop", {"max_hops": 0}),
        ("hybrid_1hop", {"max_hops": 1}),
        ("full_pipeline_2hop", {"max_hops": 2}),
    ]

    report: Dict[str, Any] = {"storage_mode": storage_mode, "modes": {}}
    for mode_name, kwargs in modes:
        latencies: List[float] = []
        recalls, precisions, mrrs, ndcgs = [], [], [], []
        for item in HELD_OUT_QUERIES:
            t0 = time.perf_counter()
            hits = retrieval_service.search(
                query=item["query"],
                tenant_id=ctx.tenant_id,
                active_scopes=[scope],
                top_k=10,
                max_hops=kwargs["max_hops"],
                now=ctx.now,
            )
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
    parser = argparse.ArgumentParser(description="KryptoDB Multi-Hop Ablation Benchmark")
    parser.add_argument("--storage-mode", choices=["plaintext", "encrypted"], default="plaintext")
    args = parser.parse_args()
    result = run_ablation(storage_mode=args.storage_mode)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
