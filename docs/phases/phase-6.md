# Phase 6: Multi-Hop and Ranking

## 1. Summary of Changes
- Implemented `kryptodb/core/retrieval/traversal.py`:
  - `MultiHopTraverser`: Bounded 1-hop and 2-hop graph expansion.
  - Cycle suppression: `s <> t AND s <> mid AND mid <> t` prevents A -> B -> A bounce-backs.
  - Hub suppression: filters high-degree intermediate nodes (`size([(mid)-[:FACT]-() | 1]) <= $max_hub_degree`).
  - Strict filter enforcement on all edges (`e1` and `e2`).
  - Pre-ordering by confidence and recency rather than premature hop-count limits.
- Implemented `kryptodb/core/retrieval/provenance.py`:
  - `ProvenanceStep`: Structured hop metadata (hop, source, predicate, target, edge_id, confidence, scope).
  - `format_citation()`: Standard citation tags `[tenant:..., scope:..., edges:e1->e2]`.
  - `format_provenance_chain()`: Human-readable chain visualization `(A)-[PRED]->(B)-[PRED]->(C)`.
- Implemented 3-stage ranking pipeline in `kryptodb/core/retrieval/ranking.py`:
  - Stage A (DB Pre-rank): Filter top 30 paths via $\text{PreScore}(P) = \text{confidence}_{\text{path}} \times \text{recency}(P)$.
  - Stage B (Cross-encoder reranking): Compute $S_{\text{cross\_encoder}} \in [0, 1]$.
  - Stage C (Optional LLM reranking): Optional flag `enable_llm_rerank` blending LLM and cross-encoder scores.
  - Scale-harmonized composite scoring:
    $$\text{FinalScore}(P) = w_{\text{rel}} \cdot S_{\text{cross\_encoder}} + w_{\text{conf}} \cdot \overline{C}(P) + w_{\text{rec}} \cdot \text{Recency}(P) - \lambda_{\text{hop}} \cdot (\text{hops} - 1)$$
    Guarantees no double-penalization of multi-hop facts and strictly bounded scores $\in [0, 1]$.
- Validated via unit tests in `tests/unit/retrieval/test_ranking.py` and `tests/unit/retrieval/test_traversal.py`.

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Unit tests
uv run pytest tests/unit/retrieval/test_ranking.py tests/unit/retrieval/test_traversal.py
```
