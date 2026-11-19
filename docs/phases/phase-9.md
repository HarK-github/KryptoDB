# Phase 9: Architectural Cleanup, Protocol Decoupling & Strict Benchmark Verification

## 1. Summary of Changes

Phase 9 addressed critical architectural debt, layering violations, and benchmark overfitting discovered during an exhaustive ponytail architectural audit of the KryptoDB memory and retrieval substrate.

### Phase 1: Hardcoded Shortcut Removal & Code Consolidation
- **Removed Hardcoded Test Shortcuts & Regression Fixtures (CRIT-1..4)**:
  - Stripped all hardcoded test entity references, user names, and test-specific heuristics from `kryptodb/core/memory/delta_extractor.py` and `turn_analysis.py`.
  - Replaced specific prompt references (`Dr. Chen`, `Alice Chen`, `Project Titan`, `Project Phoenix`, `Elena Rostova`, `CockroachDB`, `Harshit Kandpal`) with generic zero-shot schema instructions (`Dr. Smith` -> `Jane Smith`, `EntityA`, `EntityB`).
  - Removed brittle regex override blocks that bypassed model inference.
- **Module Consolidation & Dead Code Pruning (CLEAN-1..7)**:
  - Consolidated duplicate `conflict_engine.py` into canonical `conflicts.py` (CLEAN-1).
  - Merged and removed dead `hybrid.py` into `hybrid_search.py` (CLEAN-2).
  - Extracted and deduplicated Reciprocal Rank Fusion into `rrf.py` (CLEAN-3).
  - Deleted obsolete `kryptodb/infrastructure/neo4j/write_engine.py` (CLEAN-4).
  - Moved `FakeTurnAnalyzer` from production code to `tests/fakes.py` (CLEAN-5).
  - Fixed deprecated imports across legacy and graph modules (CLEAN-6).
  - Safely removed untracked temporary scratch scripts from `scripts/` (CLEAN-7).

### Phase 2: Protocol Decoupling & Cypher Centralization
- **Strict Protocol Compliance for Entity Discovery (CRIT-5 & MED-4)**:
  - Added `search_entities(self, query: str, context: RequestContext, limit: int = 20) -> List[EntityRecord]` and `add_entity(...)` to the `MemoryStore` protocol in `kryptodb/core/ports.py`.
  - Eliminated `hasattr(store, "entities")` reflection hacks from `kryptodb/core/memory/entity_resolution.py`. Entity discovery now cleanly invokes `store.search_entities()`.
  - Implemented `search_entities` and aligned `add_entity` in both `Neo4jMemoryStore` and `FakeMemoryStore`.
- **Clean Privacy & Revocation Isolation (CRIT-6)**:
  - Added `revoke_by_user(self, user_id: str, context: RequestContext) -> int` to `MemoryStore`.
  - Extracted raw Cypher queries and `hasattr(self.store, "facts")` inspection out of `MemoryService.forget_user()`, delegating directly to `self.store.revoke_by_user()`.
- **Cypher Query Centralization & Index Scan Optimization (MED-1 & MED-2)**:
  - Centralized all Cypher queries from `service.py`, `traversal.py`, and `hybrid_search.py` into `kryptodb/infrastructure/neo4j/queries.py`:
    - `REVOKE_BY_USER_QUERY`, `SEARCH_ENTITIES_PREFIX_QUERY`, `ADD_ENTITY_QUERY`
    - `RETROSPECTIVE_FACTS_QUERY`
    - `TRAVERSAL_1HOP_QUERY`, `TRAVERSAL_2HOP_EXPANSION_QUERY`
    - `HYBRID_VECTOR_ENTITY_QUERY`, `HYBRID_VECTOR_ENTITY_ALT_QUERY`, `HYBRID_FULLTEXT_ENTITY_QUERY`
    - `SEMANTIC_SEED_FALLBACK_ENTITY_QUERY`, `SEMANTIC_SEED_FALLBACK_FACT_QUERY`
  - Removed the unindexed `toLower(n.name) CONTAINS toLower($term)` full-graph scan fallback in `hybrid_search.py`.
- **Test Suite Remediation**:
  - Refactored 14 unit and integration tests that failed after removing hardcoded shortcuts. Replaced brittle fixtures with `StubExtractionLLM` and added pre-test static relationship cleanup fixtures (`clean_neo4j_static_edges`, `clean_static_edges`) for idempotent testing.
  - Achieved **197 passed, 0 failures** across the full pytest suite.

---

## 2. Strict Benchmark Verification Results

Evaluated against `scripts/eval_kryptodb_strict.py` using `meta-llama/llama-3.3-70b-instruct` via OpenRouter (with cache cleared at startup):

| Test | Objective | Status | Latency | Result Highlights |
| :--- | :--- | :---: | :---: | :--- |
| **Test 1** | Unstructured Co-Reference & Canonical Alias Extraction | **PASSED** | 290.83ms | 100% resolution of pronouns/aliases (`She`, `Dr. Chen`) to canonical `Alice Chen`. |
| **Test 2** | Directed Dependency Polarity (Upstream vs Downstream) | **PASSED** | 10.70ms | Preserves causal edge directionality without subject/object inversion. |
| **Test 3** | 2-Hop Bridge Discovery (Semantic Seed Fallback) | **PASSED** | 26.78ms | Discovers 2-hop bridge entities even with zero query-to-entity lexical overlap. |
| **Test 4** | Bi-Temporal State Supersession & Retrospective Routing | **PASSED** | 22.59ms | Distinguishes current state (PostgreSQL 16) vs historical state (PostgreSQL 14). |
| **Test 5** | Dense Hub Distractor Suppression (500 Distractors) | **PASSED** | 231.93ms | Sub-second extraction of needle facts (`API_Key_99`) amid 500 noise events. |

---

## 3. Verification Commands

```bash
# 1. Full Unit & Integration Test Suite (197 tests, 0 failures)
uv run pytest

# 2. Strict Evaluation Benchmark (Meta Llama 3.3 70B Instruct)
uv run python scripts/eval_kryptodb_strict.py

# 3. Import Architecture Linter
uv run lint-imports
```
