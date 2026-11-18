# Phase 5: Hybrid Search

## 1. Summary of Changes
- Implemented `kryptodb/core/retrieval/query.py`:
  - Deterministic, sub-millisecond query parsing (`parse_query()`).
  - Stopword filtering, quoted entity detection, and temporal token identification without runtime LLM calls.
- Implemented `kryptodb/core/retrieval/hybrid.py`:
  - Reciprocal Rank Fusion (`reciprocal_rank_fusion()`) fusing arbitrary $M$ ranking lists with configurable $k=60$.
  - `HybridSearcher`: Orchestrates exact entity match, dense vector search, and sparse BM25 fulltext search, all filtered through `FilterBuilder`.
- Implemented unit tests in `tests/unit/retrieval/test_hybrid.py`:
  - Mathematical correctness of RRF rank combination.
  - Priority ranking for exact name matches.
  - Multi-word sparse text retrieval via BM25.
  - Strict tenant boundary isolation (zero leakage of foreign records).
  - Empty scope blocking (zero records returned when active_scopes is empty).

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Unit tests
uv run pytest tests/unit/retrieval/test_hybrid.py
```
