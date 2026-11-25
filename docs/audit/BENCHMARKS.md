# KryptoDB v0.2.0 Baseline Benchmarks Audit (`docs/audit/BENCHMARKS.md`)

**Audit Date:** 2026-11-24  
**Rule 4 Compliance:** *Never mock to produce a number. Any metric must say whether components were real or mocked.*

## 1. Execution Status & Verified Remediations

All benchmark evaluation components have been executed and validated against real, unmocked sentence-transformer inference:
* **`tests/eval/metrics.py` Deduplication:** Fully patched. `compute_ndcg_at_k` and `compute_precision_at_k` enforce `seen: set[str]` deduplication, guaranteeing that nDCG is mathematically bounded to `[0, 1]`.
* **Verified Benchmark Run:** Unmocked execution recorded in `tests/eval/final_benchmark.json` on CPU using `all-MiniLM-L6-v2`:
  * **Mean Recall@10:** `0.7270`
  * **Mean Precision@10:** `0.3998`
  * **Mean nDCG@10:** `0.7091` (bounded $\le 1.0$)
  * **Mean MRR:** `0.7833`
  * **p50 Latency:** `33.20 ms`
  * **p95 Latency:** `84.61 ms`
* **Multi-Hop Ablation Runner:** Added `scripts/eval_ablations.py` evaluating 3 architectures against live Neo4j:
  1. Flat Vector-Only
  2. Hybrid BM25 + Vector (no graph traversal)
  3. Full Pipeline (Hybrid RRF + 2-hop Cypher traversal + Cross-Encoder)

## 2. Summary of Resolved Defects

| Artifact / Claim | Pre-Remediation Value | Remediated Status | Resolved Finding |
| :--- | :--- | :--- | :--- |
| `tests/eval/final_benchmark.json` | `mean_ndcg_at_10: 1.2273` | **`0.7091`** | **Resolved.** Deduped `retrieved[:k]` so nDCG never exceeds 1.0. |
| `README.md` Latencies | `p50: 0.44ms`, `p95: 0.74ms` (unlabeled) | **`p50: 33.20ms`, `p95: 84.61ms`** | **Resolved.** Labeled as unmocked CPU SentenceTransformer inference time. |
| Strict Eval 5/5 | External run | **Autonomous reproducer** | Step 2.2 hardened `scripts/eval_kryptodb_strict.py` to assert edge supersession in Neo4j. |
