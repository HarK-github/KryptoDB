# KryptoDB v0.2.0 Claims Ledger (`docs/audit/CLAIMS_LEDGER.md`)

**Audit Date:** 2026-11-24  
**Source Audited:** `kryptodb.zip` (`README.md`, `docs/ARCHITECTURE.md`, `docs/MIGRATION_MAP.md`, `tests/eval/*.json`)

| # | Claim | Source | Status | Verified Code Evidence |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **"Overall Mean nDCG@10: 1.2273 ✅ Passed (+23.13%)"** | `README.md:126`, `tests/eval/final_benchmark.json` | **REMEDIATED** | NDCG is mathematically bounded to `[0, 1]`. Patched `tests/eval/metrics.py:25-36` with item deduplication; unmocked benchmark re-run yields `0.7091`. |
| 2 | **"p50 Latency: 0.44 ms, p95: 0.74 ms" / "sub-50ms latency"** | `README.md:133-134`, `docs/ARCHITECTURE.md:171` | **REMEDIATED** | Updated `README.md` to reflect unmocked CPU `SentenceTransformer` inference times (`p50: 33.20ms`, `p95: 84.61ms`). |
| 3 | **"Zero-leakage security at the database level" / "Single Choke Point for All Queries"** | `README.md:103`, `docs/ARCHITECTURE.md:20, 107-115` | **REMEDIATED** | Integrated `PolicyEngine` (`policy.py`), fail-closed admin key requirement, removed `dev-admin-key`, and enforced thread ownership on `/chat/{id}/history`. |
| 4 | **"Strictly adheres to Hexagonal Architecture (Ports and Adapters), completely isolating domain logic"** | `docs/ARCHITECTURE.md:28`, `README.md:41, 225` | **VERIFIED** | Enforced via `.importlinter` contract (`Contracts: 2 kept, 0 broken`). Core domain has zero imports from infrastructure or external drivers. |
| 5 | **"Bi-Temporal Memory Lifecycle"** | `README.md:21`, `docs/ARCHITECTURE.md:15` | **REMEDIATED** | Edges store `valid_from`, `valid_until`, `created_at`, and `FilterBuilder` filters on `valid_until IS NULL` for active graph queries. |
| 6 | **"Atomic supersession"** | `README.md:69`, `docs/ARCHITECTURE.md:144` | **REMEDIATED** | `ATOMIC_SUPERSEDE_QUERY` acquires `old_r._lock = true`, verifies `old_r.status = 'active'`, stamps `superseded_by`, and sets `valid_until = $now` in a single Cypher transaction. Verified by 100x concurrency test. |
| 7 | **"Durable SQLite Checkpointer"** | `README.md:38, 78`, `docs/ARCHITECTURE.md:21` | **VERIFIED** | `SqliteSaver` supported via `get_agent_checkpointer()`. |
| 8 | **"Dense cosine similarity and BM25 keyword matching execute in parallel"** | `README.md:104`, `docs/ARCHITECTURE.md:17` | **PARTIAL** | Executed sequentially or fused via Reciprocal Rank Fusion (`rrf.py`). |
| 9 | **"Audit log / Event log"** | `README.md:170`, `daemon/app.py:68` | **REMEDIATED** | Implemented `AuditLedger` (`infrastructure/audit_ledger.py`) writing append-only SHA-256 hash chains to `data/audit.db`. |
| 10 | **`USES` and `DEPLOYED_ON` are `Cardinality.MANY`** | `docs/ARCHITECTURE.md:162` | **REMEDIATED** | Both `USES` and `DEPLOYED_ON` are confirmed as `Cardinality.ONE` in `predicates.py` and documentation is updated. |
| 11 | **Vector indexes named `entity_vector_index` and `fact_vector_index`** | `README.md:102` | **REMEDIATED** | Aligned documentation with `schema.py` index names: `entity_embedding_idx` and `fact_embedding_idx`. |
| 12 | **"Passing" tests count** | `README.md:13, 228` | **VERIFIED** | Test suite expanded to **225 tests** across unit, crypto v3, security, and eval suites. All pass. |
| 13 | **"Strict Eval: 5/5 Passed (Meta-Llama 3.3 70B)"** | `README.md:14, 141-156` | **VERIFIED** | Step 2.2 tightened `scripts/eval_kryptodb_strict.py` to assert actual Neo4j edge supersession state in database. |
| 14 | **Quote verification rejects hallucinated attributions** | `README.md:114`, `docs/ARCHITECTURE.md:152` | **VERIFIED** | `write_policy.py:179-190` enforces `quote_substring.strip() in full_turn_text`. |
| 15 | **Scale-harmonized composite reranking & cycle-safe 2-hop traversal** | `README.md:106-108` | **VERIFIED** | `ranking.py:106-160` implements 3-stage ranking with sigmoid-normalized `CrossEncoder`, confidence, recency decay, and hop penalty. |
| 16 | **`WORKS_FOR` Predicate Cardinality** | `docs/adr/0002-predicate-cardinality.md`, `predicates.py:41` | **VERIFIED** | Established as `Cardinality.ONE` (functional primary employment) per ADR 0002. Coexisting multi-affiliations use `MEMBER_OF` or `CONTRIBUTES_TO` (`Cardinality.MANY`). |
