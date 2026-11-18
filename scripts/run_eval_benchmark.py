#!/usr/bin/env python3
"""
scripts/run_eval_benchmark.py
Executes the gold benchmark query suite (tests/eval/queries.jsonl),
evaluates metrics (Recall@10, Precision@10, nDCG@10, MRR, p50/p95 latency),
and outputs the baseline report to tests/eval/baseline.json.
"""
import os
import sys
import time
import json
import statistics
from pathlib import Path
from datetime import datetime, timezone

from tests.eval.metrics import compute_recall_at_k, compute_precision_at_k, compute_ndcg_at_k, compute_mrr
from tests.eval.test_gold_benchmark import InMemoryGraphStore
from kryptodb.core.retrieval.service import RetrievalService
from kryptodb.core.retrieval.hybrid_search import HybridSearchEngine
from kryptodb.core.retrieval.multihop import MultiHopTraverser
from kryptodb.core.retrieval.ranking import PathReranker

def setup_demo_scenario_graph() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    
    # Phase 1: Acme Organization
    store.add_fact("e1", "Alice", "WORKS_FOR", "Acme Corp", scope="ORG:acme-structure")
    store.add_fact("e2", "Bob", "WORKS_FOR", "Acme Corp", scope="ORG:acme-structure")
    store.add_fact("e3", "Alice", "MANAGES", "Bob", scope="ORG:acme-structure", confidence=0.95)
    store.add_fact("e4", "Alice", "ASSIGNED_TO", "Engineering Department", scope="ORG:acme-structure")
    store.add_fact("e5", "Bob", "ASSIGNED_TO", "Engineering Department", scope="ORG:acme-structure")

    # Phase 2: Sensitive Project Data
    store.add_fact("e6", "Project Alpha", "RELIES_ON", "Database Cluster X", scope="PROJECT:alpha")
    store.add_fact("e7", "Database Cluster X", "CONTAINS", "Customer PII", scope="PROJECT:alpha")
    store.add_fact("e8", "Bob", "HAS_ADMIN_ACCESS", "Database Cluster X", scope="PROJECT:alpha", confidence=0.98)
    store.add_fact("e9", "Alice", "OWNS", "Project Alpha", scope="PROJECT:alpha")

    # Phase 3: Threat Intelligence
    store.add_fact("e10", "Suspicious IP 192.168.1.50", "ATTACKED", "Database Cluster X", scope="RESEARCH:threat-intel")
    store.add_fact("e11", "Suspicious IP 192.168.1.50", "ASSOCIATED_WITH", "Bob", scope="RESEARCH:threat-intel")
    store.add_fact("e12", "Data Exfiltration Alert", "TRIGGERED_ON", "Database Cluster X", scope="RESEARCH:threat-intel")

    # Superseded & History
    store.add_fact("e13", "Alice", "LIVES_IN", "New York", status="superseded", confidence=0.8)
    store.add_fact("e14", "Alice", "LIVES_IN", "San Francisco", status="active", confidence=0.95)

    return store

def run_benchmark():
    queries_path = Path("tests/eval/queries.jsonl")
    if not queries_path.exists():
        print(f"Error: {queries_path} not found.")
        sys.exit(1)

    queries = []
    with open(queries_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                queries.append(json.loads(line))

    store = setup_demo_scenario_graph()
    retrieval_service = RetrievalService(
        hybrid_engine=HybridSearchEngine(client=store),
        traverser=MultiHopTraverser(client=store),
        reranker=PathReranker()
    )

    recalls, precisions, ndcgs, mrrs, latencies = [], [], [], [], []
    category_scores = {}

    for q in queries:
        tenant_id = q.get("tenant_id", "tenant-default")
        expected_facts = set(q.get("expected_fact_ids", []))
        expected_entities = set(q.get("expected_entity_ids", []))
        ground_truth = expected_facts | expected_entities
        q_type = q.get("type", "general")

        t0 = time.perf_counter()
        results = retrieval_service.search(
            query=q["query"],
            tenant_id=tenant_id,
            top_k=10,
            max_hops=2
        )
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        latencies.append(latency_ms)

        retrieved_ids = []
        for r in results:
            retrieved_ids.append(r.fact_id)
            for step in r.provenance_path:
                retrieved_ids.append(step.source)
                retrieved_ids.append(step.target)
                retrieved_ids.append(step.edge_id)

        rec = compute_recall_at_k(retrieved_ids, ground_truth, k=10)
        prec = compute_precision_at_k(retrieved_ids, ground_truth, k=10)
        ndcg = compute_ndcg_at_k(retrieved_ids, ground_truth, k=10)
        mrr = compute_mrr(retrieved_ids, ground_truth)

        recalls.append(rec)
        precisions.append(prec)
        ndcgs.append(ndcg)
        mrrs.append(mrr)

        if q_type not in category_scores:
            category_scores[q_type] = {"recall": [], "precision": [], "ndcg": [], "mrr": []}
        category_scores[q_type]["recall"].append(rec)
        category_scores[q_type]["precision"].append(prec)
        category_scores[q_type]["ndcg"].append(ndcg)
        category_scores[q_type]["mrr"].append(mrr)

    mean_recall = statistics.mean(recalls)
    mean_precision = statistics.mean(precisions)
    mean_ndcg = statistics.mean(ndcgs)
    mean_mrr = statistics.mean(mrrs)
    p50_lat = statistics.median(latencies)
    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95)
    p95_lat = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]

    cat_breakdown = {}
    for cat, metrics in category_scores.items():
        cat_breakdown[cat] = {
            "queries_count": len(metrics["recall"]),
            "recall_at_10": round(statistics.mean(metrics["recall"]), 4),
            "precision_at_10": round(statistics.mean(metrics["precision"]), 4),
            "ndcg_at_10": round(statistics.mean(metrics["ndcg"]), 4),
            "mrr": round(statistics.mean(metrics["mrr"]), 4)
        }

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_queries": len(queries),
        "mean_recall_at_10": round(mean_recall, 4),
        "mean_precision_at_10": round(mean_precision, 4),
        "mean_ndcg_at_10": round(mean_ndcg, 4),
        "mean_mrr": round(mean_mrr, 4),
        "p50_latency_ms": round(p50_lat, 2),
        "p95_latency_ms": round(p95_lat, 2),
        "category_breakdown": cat_breakdown
    }

    out_file = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "tests/eval/final_benchmark.json"
    if "--baseline" in sys.argv:
        out_file = "tests/eval/baseline.json"
    out_path = Path(out_file)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Benchmark results written to {out_path}")
    print(json.dumps(report, indent=2))
    return report

if __name__ == "__main__":
    run_benchmark()
