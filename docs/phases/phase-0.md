# Phase 0: Safety Net, Baseline, & Capability Verification

## 1. Summary of Changes
- Established automated evaluation suite in `tests/eval/queries.jsonl` containing 50 gold queries across 7 categories: exact-entity, synonym, multi-hop, conversation-recall, superseded-fact, negative, and tenant isolation probes.
- Implemented `compute_mrr` in `tests/eval/metrics.py` alongside Recall@K, Precision@K, and nDCG@K.
- Built and ran `scripts/check_neo4j_capabilities.py` verifying Neo4j version and index support.
- Documented findings in `docs/adr/0001-fact-storage-shape.md`: Neo4j 5.21.2 Community natively supports relationship vector indexing, confirming direct `(:Entity)-[:FACT]->(:Entity)` storage without reified nodes.
- Built and ran `scripts/run_eval_benchmark.py` evaluating all 50 queries and generating `tests/eval/baseline.json`.
- Configured `.importlinter` enforcing layered clean architecture (`interfaces -> core`) and core isolation. Verified with `uv run lint-imports` (2 contracts kept, 0 broken).
- Ensured 100% of unit tests pass (90 passed in 26.8s).

## 2. Capability Check Result
```json
{
  "connected": true,
  "version": "5.21.2",
  "edition": "community",
  "node_vector_index_supported": true,
  "relationship_vector_index_supported": true,
  "reified_fact_required": false,
  "errors": []
}
```

## 3. Baseline Evaluation Metrics (`tests/eval/baseline.json`)
* **Total Queries:** 50
* **Mean Recall@10:** `0.7455` (74.55%)
* **Mean Precision@10:** `0.3840` (38.40%)
* **Mean nDCG@10:** `0.9960`
* **Mean MRR:** `0.7462`
* **Latency:** p50 = `0.32 ms`, p95 = `0.46 ms`

### Category Breakdown
| Category | Queries | Recall@10 | Precision@10 | nDCG@10 | MRR |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **exact-entity** | 10 | 0.8933 | 0.6300 | 1.4095 | 1.0000 |
| **synonym** | 10 | 0.7833 | 0.3400 | 0.9322 | 0.5311 |
| **multi-hop** | 10 | 0.5425 | 0.5500 | 0.8061 | 0.6667 |
| **conversation-recall** | 5 | 0.7167 | 0.5000 | 1.0931 | 0.7667 |
| **superseded-fact** | 5 | 0.5000 | 0.3000 | 0.7711 | 0.5000 |
| **negative** | 5 | 0.8000 | 0.0000 | 0.8000 | 0.8000 |
| **isolation** | 5 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |

## 4. How to Run the Tests
```bash
# Run capability check
uv run python scripts/check_neo4j_capabilities.py

# Run eval benchmark
PYTHONPATH=. uv run python scripts/run_eval_benchmark.py

# Run import architecture linter
uv run lint-imports

# Run full unit test suite
KRYPTODB_MASTER_KEY=000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f uv run pytest tests/unit/
```

## 5. Known Gaps
- Core package still needs final separation of `core/context.py`, `core/filters.py`, `core/episodic/` (events & episodes), and `core/ports.py` (Phase 1–4).
- Shims currently protect legacy callers during incremental phase transitions.
