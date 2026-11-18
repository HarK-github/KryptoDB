from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Dict, List, Any, Set
from datetime import datetime, timezone

from tests.eval.metrics import compute_recall_at_k, compute_precision_at_k, compute_ndcg_at_k
from kryptodb.core.retrieval.service import RetrievalService
from kryptodb.core.retrieval.hybrid_search import HybridSearchEngine
from kryptodb.core.retrieval.multihop import MultiHopTraverser
from kryptodb.core.retrieval.ranking import PathReranker

class InMemoryGraphStore:
    """Fast in-memory mock graph store representing Neo4j for gold benchmark eval."""
    def __init__(self):
        self.entities = {} # id -> {id, name, tenant_id}
        self.edges = []    # [{id, s_id, s_name, t_id, t_name, predicate, desc, tenant_id, status, ...}]

    def add_entity(self, eid: str, name: str, tenant_id: str = "tenant-default"):
        self.entities[eid] = {"id": eid, "name": name, "tenant_id": tenant_id}

    def add_fact(
        self,
        fid: str,
        s_name: str,
        predicate: str,
        t_name: str,
        tenant_id: str = "tenant-default",
        status: str = "active",
        confidence: float = 1.0,
        scope: str = "default"
    ):
        s_id = f"ent-{s_name.casefold().replace(' ', '_')}"
        t_id = f"ent-{t_name.casefold().replace(' ', '_')}"
        self.add_entity(s_id, s_name, tenant_id)
        self.add_entity(t_id, t_name, tenant_id)
        self.edges.append({
            "id": fid,
            "s_id": s_id,
            "s_name": s_name,
            "t_id": t_id,
            "t_name": t_name,
            "predicate": predicate,
            "description": f"{s_name} {predicate} {t_name}",
            "tenant_id": tenant_id,
            "status": status,
            "confidence": confidence,
            "trust_weight": 1.0,
            "scope": scope,
            "created_at": datetime.now(timezone.utc).isoformat()
        })

    def execute_query_with_retry(self, query: str, **params):
        tenant_id = params.get("tenant_id", "tenant-default")
        
        # 1. Seed entity match (vector or fulltext query simulation)
        if "entity_vector_index" in query or "entity_name_ft" in query or "CONTAINS" in query:
            raw_lucene = str(params.get("lucene_query") or params.get("term") or "").lower()
            tokens = [t.replace("*", "") for t in raw_lucene.split(" or ") if t.replace("*", "")]
            matches = []
            for e in self.entities.values():
                if e["tenant_id"] == tenant_id:
                    e_name = e["name"].lower()
                    score = 0.0
                    for tok in tokens:
                        if tok == e_name:
                            score += 15.0 # Exact full name match
                        elif tok in e_name:
                            score += 2.0
                    if score > 0:
                        matches.append({"id": e["id"], "name": e["name"], "vector_score": score, "bm25_score": score})
            matches.sort(key=lambda x: x["vector_score"], reverse=True)
            return matches[:params.get("top_k", 10)], None, None

        # 2. 1-hop traversal simulation (bidirectional)
        if "MATCH (s:Entity)-[e:FACT]-(t:Entity)" in query:
            seed_ids = set(params.get("seed_ids", []))
            target_status = params.get("status", "active")
            res = []
            for e in self.edges:
                if e["tenant_id"] == tenant_id and (e["s_id"] in seed_ids or e["t_id"] in seed_ids) and e["status"] == target_status:
                    res.append({
                        "s_id": e["s_id"], "s_name": e["s_name"],
                        "t_id": e["t_id"], "t_name": e["t_name"],
                        "edge_id": e["id"], "predicate": e["predicate"],
                        "description": e["description"], "confidence": e["confidence"],
                        "trust_weight": e["trust_weight"], "scope": e["scope"],
                        "created_at": e["created_at"]
                    })
            return res[:params.get("limit_1hop", 10)], None, None

        # 3. 2-hop traversal simulation (bidirectional)
        if "e1:FACT" in query and "e2:FACT" in query:
            seed_ids = set(params.get("seed_ids", []))
            target_status = params.get("status", "active")
            res = []
            for e1 in self.edges:
                if e1["tenant_id"] == tenant_id and e1["status"] == target_status:
                    if e1["s_id"] in seed_ids:
                        mid_id = e1["t_id"]
                    elif e1["t_id"] in seed_ids:
                        mid_id = e1["s_id"]
                    else:
                        continue

                    for e2 in self.edges:
                        if e2["tenant_id"] == tenant_id and e2["status"] == target_status and e2["id"] != e1["id"]:
                            other_id = None
                            if e2["s_id"] == mid_id:
                                other_id = e2["t_id"]
                            elif e2["t_id"] == mid_id:
                                other_id = e2["s_id"]

                            if other_id and other_id != e1["s_id"] and other_id != e1["t_id"]:
                                res.append({
                                    "s_id": e1["s_id"], "s_name": e1["s_name"],
                                    "mid_id": mid_id, "mid_name": e1["t_name"],
                                    "t_id": other_id, "t_name": e2["t_name"] if e2["t_id"] == other_id else e2["s_name"],
                                    "e1_id": e1["id"], "e1_predicate": e1["predicate"], "e1_desc": e1["description"],
                                    "e1_conf": e1["confidence"], "e1_scope": e1["scope"],
                                    "e2_id": e2["id"], "e2_predicate": e2["predicate"], "e2_desc": e2["description"],
                                    "e2_conf": e2["confidence"], "e2_scope": e2["scope"],
                                    "e2_created_at": e2["created_at"]
                                })
            return res[:params.get("limit_2hop", 15)], None, None

        return [], None, None

def populate_gold_facts(graph: InMemoryGraphStore):
    facts = [
        ("f01", "Alice", "ASSIGNED_TO", "Engineering Department", "tenant-default", "active"),
        ("f02", "Alice", "MANAGES", "Bob", "tenant-default", "active"),
        ("f03", "Bob", "HAS_ADMIN_ACCESS", "Database Cluster X", "tenant-default", "active"),
        ("f04", "Alice", "OWNS", "Project Alpha", "tenant-default", "active"),
        ("f05", "Database Cluster X", "CONTAINS", "Customer PII", "tenant-default", "active"),
        ("f06", "Suspicious IP 192.168.1.50", "ATTACKED", "Database Cluster X", "tenant-default", "active"),
        ("f07", "Suspicious IP 192.168.1.50", "ASSOCIATED_WITH", "Bob", "tenant-default", "active"),
        ("f08", "Project Alpha", "RELIES_ON", "Database Cluster X", "tenant-default", "active"),
        ("f09", "Database Cluster X", "HOSTED_ON", "AWS us-east-1", "tenant-default", "active"),
        ("f10", "Security Incident 404", "TARGETS", "Database Cluster X", "tenant-default", "active"),
        ("f11", "Charlie", "INVESTIGATES", "Security Incident 404", "tenant-default", "active"),
        ("f12", "Alice", "WORKS_FOR", "Acme Corp", "tenant-default", "active"),
        ("f13", "Alice", "CONTRIBUTED_TO", "LangChain", "tenant-default", "active"),
        ("f14", "Bob", "LIVES_IN", "San Francisco", "tenant-default", "active"),
        ("f15", "Diana", "IS_CEO_OF", "Acme Corp", "tenant-default", "active"),
        ("f16", "Bob", "LIVES_IN", "New York", "tenant-default", "superseded"),
        ("f17", "Bob", "HOLDS_LICENSE", "License-99", "tenant-default", "active"),
        ("f18", "Bob", "HELD_CERTIFICATE", "Legacy-Cert-2025", "tenant-default", "deprecated"),
        ("f19", "Eve", "ACCESSES", "Beta Server 1", "tenant-beta", "active"),
        ("f20", "Alice Smith", "HAS_EMAIL", "alice.smith@acme.com", "tenant-default", "active"),
        ("f21", "Alice Johnson", "HAS_ROLE", "Product Manager", "tenant-default", "active"),
        ("f22", "Billing Service", "CONNECTS_TO", "Database Cluster X", "tenant-default", "active"),
        ("f23", "Auth Service", "CONNECTS_TO", "Database Cluster X", "tenant-default", "active"),
        ("f24", "Finance Ops Team", "OPERATES", "Billing Service", "tenant-default", "active"),
        ("f25", "Bob", "HAD_ACCESS", "Staging Server 3", "tenant-default", "revoked"),
        ("f26", "Bob", "PROFICIENT_IN", "Python", "tenant-default", "active"),
    ]
    for fid, s, p, t, tid, st in facts:
        graph.add_fact(fid, s, p, t, tenant_id=tid, status=st)

def test_gold_benchmark_evaluation_suite():
    graph = InMemoryGraphStore()
    populate_gold_facts(graph)

    service = RetrievalService(
        hybrid_engine=HybridSearchEngine(client=graph),
        traverser=MultiHopTraverser(client=graph),
        reranker=PathReranker()
    )

    benchmark_path = Path(__file__).parent / "gold_benchmark.jsonl"
    with open(benchmark_path, "r", encoding="utf-8") as fh:
        queries = [json.loads(line) for line in fh if line.strip()]

    recall_list = []
    precision_list = []
    ndcg_list = []

    for q in queries:
        qid = q["id"]
        q_text = q["query"]
        category = q.get("category", "")
        tenant_id = q.get("tenant_id", "tenant-default")
        target_entities = set(q.get("target_entities", []))

        results = service.search(
            query=q_text,
            tenant_id=tenant_id,
            top_k=10,
            max_hops=q.get("expected_hops", 2)
        )

        retrieved_entities = []
        for r in results:
            for step in r.provenance_path:
                retrieved_entities.append(step.source)
                retrieved_entities.append(step.target)

        # Handling negative / isolation queries:
        if category in {"tenant-negative", "negative-not-found"}:
            forbidden = {"Beta Server 1", "Project Zephyr"}
            leaked = set(retrieved_entities) & forbidden
            r_k = 1.0 if not leaked else 0.0
            p_k = 1.0 if not leaked else 0.0
            ndcg_k = 1.0 if not leaked else 0.0
        elif category in {"temporal-superseded", "temporal-expired", "revocation"}:
            leaked_status = {"New York", "Legacy-Cert-2025", "Staging Server 3"} & set(retrieved_entities)
            r_k = 1.0 if not leaked_status else 0.0
            p_k = 1.0 if not leaked_status else 0.0
            ndcg_k = 1.0 if not leaked_status else 0.0
        else:
            r_k = compute_recall_at_k(retrieved_entities, target_entities, k=10)
            p_k = compute_precision_at_k(retrieved_entities, target_entities, k=10)
            ndcg_k = compute_ndcg_at_k(retrieved_entities, target_entities, k=10)

        recall_list.append(r_k)
        precision_list.append(p_k)
        ndcg_list.append(ndcg_k)

    mean_recall = sum(recall_list) / len(recall_list)
    mean_precision = sum(precision_list) / len(precision_list)
    mean_ndcg = sum(ndcg_list) / len(ndcg_list)

    print(f"\n=======================================================")
    print(f"📊 KryptoDB Gold Benchmark Results (25 Queries)")
    print(f"=======================================================")
    print(f"Mean Recall@10:    {mean_recall:.4f}")
    print(f"Mean Precision@10: {mean_precision:.4f}")
    print(f"Mean nDCG@10:      {mean_ndcg:.4f}")
    print(f"=======================================================")

    assert mean_recall >= 0.85, f"Expected Mean Recall >= 0.85, got {mean_recall:.4f}"
    assert mean_ndcg >= 0.80, f"Expected Mean nDCG >= 0.80, got {mean_ndcg:.4f}"
