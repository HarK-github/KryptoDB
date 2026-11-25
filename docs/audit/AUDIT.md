# KryptoDB v0.2.0 Full Architecture Audit & Patching Plan (`docs/audit/AUDIT.md`)

**Audit Date:** 2026-11-24  
**Mode:** Read-Only Full Architecture Scan (zero production code modified)  
**Source Audited:** `kryptodb.zip` (v0.2.0 two-stack repository; 190 `test_*` functions; `.importlinter`; live entrypoint `kryptodb.daemon.app:app`)

## 1. Reachability Check (Live Stack vs. Dead Old Stack)

Verified from `docker-entrypoint.sh:34`, `Makefile:12`, `README.md:208`, and `kryptodb/daemon/app.py`:

| Stack | Entrypoint / Modules | Status |
| :--- | :--- | :--- |
| **Live v0.2.0 Stack (Mounted)** | `kryptodb.daemon.app:app` → `interfaces/http/routes/{health,v1_routes,memory,admin,graph,chat}.py`, `interfaces/http/middleware/context.py`, `interfaces/mcp/server.py`, `core/*`, `infrastructure/neo4j/*`, `agent_runtime/langgraph/graph.py`, `workers/embedding_retry.py` | **ACTIVE** (Plaintext GraphRAG with `tenant_id`, 384-dim vectors, BM25, 2-hop traversal). |
| **Old v0.1.0 Stack (Unmounted)** | `daemon/server.py`, `daemon/mcp_server.py`, `graph/{read_engine,write_engine}.py`, `memory_api/service.py`, `agent/graph.py`, `integrity/partition_crypto.py`, `legacy/crypto/partition_crypto.py` | **DEAD / LEGACY** — still in tree and imported only by backward-compat shims, `daemon/app.py:25` (`bootstrap_storage`, `neo4j_client`), and `agent_runtime/langgraph/graph.py:146` fallback. |

## 2. Ranked Architectural & Security Findings (v0.2.0 Live Stack)

### P0 — Critical (Auth Bypass, Cross-Tenant Leakage, Silent Data Corruption)

| ID | Finding | Location | Verified Evidence & Impact |
| :--- | :--- | :--- | :--- |
| **P0-1** | **Hardcoded `"dev-admin-key"` backdoor active even in production** | `kryptodb/core/identity/authentication.py:89-90` | `elif secrets.compare_digest(token, "dev-admin-key"): is_admin = True` runs unconditionally even when `KRYPTODB_ENV=production` and `KRYPTODB_ADMIN_API_KEY` is set. Any caller presenting `"dev-admin-key"` gets `is_admin=True, scopes=["*"]`. |
| **P0-2** | **Unauthenticated `/v1/*` access & caller-controlled `X-Tenant-ID` spoofing** | `kryptodb/interfaces/http/middleware/context.py:13-43` | `get_request_context` **never raises 401** when `claims is None` (even in production) and takes `tenant_id = x_tenant_id or ...` directly from the untrusted `X-Tenant-ID` header. Any unauthenticated client can read, write, or GDPR-delete another tenant's data via `/v1/memory/*` and `DELETE /v1/users/{user_id}/memories`. |
| **P0-3** | **Unauthenticated live read/write/WS/config endpoints** | `kryptodb/daemon/app.py:71-132`; `interfaces/http/routes/chat.py:132, 167, 320`; `routes/memory.py:83` | `POST /memory/{agent_id}/query`, `POST /memory`, `POST /memory/remember`, `WS /chat/{thread_id}/stream`, `GET /models`, `POST /config/model`, and `GET /ontology` have **zero authentication**. |
| **P0-4** | **Admin privilege escalation & plaintext API key leak** | `kryptodb/interfaces/http/routes/admin.py:43-64`; `core/identity/authentication.py:61-72` | `POST /admin/agents`, `GET /admin/api-keys`, and `POST /admin/api-keys/{key_id}/revoke` check `require_auth` without checking `auth.get("is_admin")`. Worse, `list_keys()` returns `"full_key": k` in plaintext, exposing every agent's raw API key to any authenticated caller. |
| **P0-5** | **Cross-tenant graph dump via `GET /graph/snapshot?tenant_id=system`** | `kryptodb/interfaces/http/routes/graph.py:13-59` | Any authenticated non-admin user can pass `?tenant_id=system` (sets `effective_tenant = None`) or `?tenant_id=victim-tenant`, bypassing `FilterBuilder` and dumping up to 500 nodes and 1,000 edges (including revoked/superseded facts) across tenants. |
| **P0-6** | **Wildcard CORS with `allow_credentials=True`** | `kryptodb/daemon/app.py:48-54` | `allow_origins=["*"]` combined with `allow_credentials=True` weakens browser origin isolation. |
| **P0-7** | **Silent write-failure swallowing in `Neo4jMemoryStore._execute`** | `kryptodb/infrastructure/neo4j/store.py:58-69, 107, 176` | `_execute` catches every exception, logs it, and returns `[]`. `write_fact`, `atomic_supersede`, and `revoke` never check the result and return success (`fact`) even when Neo4j is down, times out, or violates a constraint. |
| **P0-8** | **Supersession race & silent no-op in `ATOMIC_SUPERSEDE_QUERY`** | `kryptodb/infrastructure/neo4j/queries.py:65-109`; `core/memory/memory_service.py:192-218` | `ATOMIC_SUPERSEDE_QUERY` does not lock `old_r`, does not assert `old_r.status = 'active'`, and if `old_r` is missing/already superseded, `MATCH` yields 0 rows so `CREATE` silently creates nothing. `correct_memory()` (`memory_service.py:204-218`) uses non-atomic `supersede()` + `write_fact()`. |
| **P0-9** | **Unscoped cross-tenant revocation & HITL bypass in `revoke_fact_tool`** | `kryptodb/agent_runtime/langgraph/graph.py:197-233` | `revoke_fact_tool` hardcodes `"approved": True` when calling `/memory/forget`, and its in-process fallback executes raw Cypher `MATCH (s:Entity {name: $subject})-[r:FACT {predicate: $predicate}]->(o:Entity)` with **zero `tenant_id` and zero `scope` filter**, revoking matching facts across all tenants. |
| **P0-10** | **Dead idempotency key & Neo4j Community constraint failure** | `kryptodb/core/memory/memory_service.py:101-125`; `infrastructure/neo4j/store.py:80-105`; `schema.py:24-26, 79-81` | `idempotency_key` is computed in `MemoryService` and placed in `metadata`, but `Neo4jMemoryStore` never writes it to Neo4j and no uniqueness constraint exists. `CREATE CONSTRAINT fact_id_unique FOR ()-[r:FACT]-() REQUIRE r.id IS UNIQUE` requires Neo4j Enterprise; on Community (`5.21.2-community`) it fails silently while `ensure_schema` still reports `"constraints": True`. |

### P1 — High (Wrong Answers, Broken Semantics, Fake Observability/Benchmarks)

| ID | Finding | Location | Verified Evidence & Impact |
| :--- | :--- | :--- | :--- |
| **P1-1** | **Property-name mismatch in `ConflictEngine.evaluate_proposal`** | `kryptodb/core/memory/conflicts.py:127-128` | Raw Cypher filters `r.permission_scope = $scope`, while `WRITE_FACT_QUERY` writes `r.scope` — scope-filtered conflict checks return 0 rows. |
| **P1-2** | **Hardcoded fake audit log & volatile HITL checkpointer** | `kryptodb/interfaces/http/routes/admin.py:66-75`; `interfaces/http/routes/chat.py:100-109` | `GET /admin/audit` returns 6 static hardcoded rows. `get_agent_checkpointer()` uses in-memory `MemorySaver()` (not SQLite), losing all HITL interrupts on restart. |
| **P1-3** | **Not bi-temporal** | `kryptodb/infrastructure/neo4j/queries.py:24-41`; `core/filters.py:39-57` | Edges have `valid_from`, `valid_until`, `created_at` only—no `recorded_from`/`recorded_to`, and `FilterBuilder.build` has no `as_of_valid` / `as_of_recorded` parameters. |
| **P1-4** | **Unknown predicates auto-registered as `Cardinality.MANY` & lossy aliases** | `kryptodb/core/memory/write_policy.py:130-133`; `core/memory/predicates.py:63, 76, 135-138` | Unknown predicates silently register as `Cardinality.MANY` instead of going to similarity/review. `UPGRADED_TO`/`MIGRATED_TO`/`SWITCHED_TO`/`OPERATES` all alias to `RUNS` (conflating events with states; `OPERATES` is `MANY` at line 63 but aliased to `RUNS` which is `ONE` at line 76). |
| **P1-5** | **Broken NDCG metric (`1.2273 > 1.0`) & fake-store benchmark numbers** | `tests/eval/metrics.py:20-36`; `tests/eval/final_benchmark.json`; `README.md:126` | `compute_ndcg_at_k` does not deduplicate `retrieved[:k]`, inflating `mean_ndcg_at_10` to `1.2273` (mathematically impossible). `p50_latency_ms: 0.44ms` reflects an in-memory fake store, not live Neo4j + real embedder/reranker. |
| **P1-6** | **Incomplete `forget()` / GDPR erasure** | `kryptodb/core/memory/memory_service.py:220-226`; `infrastructure/neo4j/queries.py:111-116, 203-208` | `revoke` / `revoke_by_user` only sets `r.status = 'revoked'` on `:FACT` edges—it leaves `:Event` nodes, `:Episode` summaries, `:Entity` embeddings, Redis entries, and LangGraph checkpointer history intact. |
| **P1-7** | **Memory poisoning / prompt-injection exposure** | `kryptodb/core/memory/write_policy.py`; `core/memory/conflicts.py:180` | No per-fact source trust tiers (`user_direct` vs `tool_output` vs `web_scrape`); `conflicts.py:180` allows any equal-confidence proposal to supersede an existing fact. |

### P2 — Medium (Layering Leaks, Performance, Ops)

| ID | Finding | Location | Verified Evidence & Impact |
| :--- | :--- | :--- | :--- |
| **P2-1** | **11 port leaks in `kryptodb/core/`** | `core/identity/authentication.py:6-7`; `core/memory/memory_service.py:17-18`; `core/memory/conflicts.py:58`; `core/retrieval/{hybrid_search,service,traversal}.py` | `core/` directly imports `fastapi`, `infrastructure.neo4j.*`, and `infrastructure.embeddings`. `.importlinter` currently does not forbid `kryptodb.infrastructure`, `fastapi`, `neo4j`, `redis`, or `sentence_transformers` in `core/`. |
| **P2-2** | **Raw Cypher outside `infrastructure/neo4j/`** | `core/memory/conflicts.py:127`; `interfaces/http/routes/graph.py:30,51`; `agent_runtime/langgraph/graph.py:226`; `workers/embedding_retry.py:28,53,81,106` | 4 new-stack modules bypass `queries.py` and `FilterBuilder`. |
| **P2-3** | **Sequential (not parallel) hybrid search & missing timeouts/rate limits** | `core/retrieval/hybrid_search.py:54-115`; `interfaces/http/routes/chat.py:46-90` | Vector and BM25 run sequentially. No request rate limits, payload size caps, LLM call timeouts, or Prometheus/OTel metrics exist. |
| **P2-4** | **Two-stack maintenance drag** | `daemon/server.py`, `graph/`, `memory_api/`, `agent/`, `integrity/`, `legacy/` | Old v0.1.0 stack remains in repo; `agent_runtime/langgraph/graph.py:146` still falls back to `kryptodb.memory_api.service`. |

## 3. Prioritized Patching Plan (Grouped by Stack)

### Group A — Live Ingress & Security Patches (Fix in Place Now)
1. **Rotate all exposed secrets** (`KRYPTODB_ADMIN_API_KEY`, `KRYPTODB_MASTER_KEY`, any LLM API keys) and remove plaintext key storage in `Authenticator._agent_keys` (store SHA-256 hashes in Redis/DB, remove `"full_key"` from `list_keys()`).
2. **Delete the `"dev-admin-key"` backdoor** in `core/identity/authentication.py:89-90` and disable unauthenticated dev fallbacks when `KRYPTODB_ENV != "development"`.
3. **Fail closed in `get_request_context()`** (`interfaces/http/middleware/context.py`): require valid bearer claims (raise `401` if missing/invalid) and derive `tenant_id` strictly from verified token claims (only allow `X-Tenant-ID` override when `is_admin=True`).
4. **Authenticate every live route:** Add `Depends(require_auth)` to `daemon/app.py:71-132`, `routes/chat.py:132, 167, 320`, and `routes/memory.py:83`; add `is_admin` enforcement to `routes/admin.py:43-64`; fix `routes/graph.py:19-21` so non-admin users cannot override `tenant_id` or pass `system`, and filter via `FilterBuilder`.
5. **Lock down CORS & ingress limits:** Replace `allow_origins=["*"]` with an env-configured allowlist; add request body size limits and per-key rate limits.

### Group B — `core/` & `infrastructure/` Correctness & Durability (Build Once in New Stack)
1. **Write failing P0 tests first:** Cross-tenant `/v1/*` and `/graph/snapshot` isolation, `dev-admin-key` rejection, concurrent 100× `Cardinality.ONE` supersession race, mid-write Neo4j error propagation, HITL stale resume, and `forget()` full-purge verification.
2. **Fix `Neo4jMemoryStore` & `queries.py`:** Propagate exceptions instead of returning `[]`; lock `old_r` (`SET old_r._lock = true`), assert `old_r.status = 'active'` and `old_r.scope = $scope`, raise `SupersessionConflictError` on 0 rows; persist and enforce `idempotency_key`; fix `correct_memory()` to use `atomic_supersede()`; remove Enterprise-only relationship uniqueness constraint from Community `schema.py` (enforce via node-locked tx or explicit check).
3. **Eliminate all 11 port leaks & 4 Cypher leaks:** Inject `MemoryStore`/`Embedder` into `MemoryService`, `ConflictEngine`, `HybridSearchEngine`, `RetrievalService`, and `Traversal`; move `graph.py`, `conflicts.py`, `embedding_retry.py`, and `revoke_fact_tool` queries into `infrastructure/neo4j/queries.py`; extend `.importlinter` to forbid `infrastructure`, `fastapi`, `neo4j`, `redis`, `langgraph`, `sentence_transformers` inside `core/`.
4. **Harden durability, deletion, & poisoning defense:** Replace `MemorySaver()` with `SqliteSaver` (with HITL prior-version revalidation, single-use, and expiry); replace fake `/admin/audit` with an append-only hash-chained ledger; extend `forget_user()`/`forget_memory()` to purge `:Event`, `:Episode`, embeddings, caches, and checkpointer threads; add per-fact source trust tiers so low-trust inputs cannot overwrite high-trust facts without approval.
5. **Real bi-temporal model & honest benchmarks:** Add `recorded_from`/`recorded_to` + `as_of_valid`/`as_of_recorded` in `FilterBuilder`; fix `compute_ndcg_at_k` deduplication in `tests/eval/metrics.py`; rerun benchmarks against real Neo4j + real `SentenceTransformer`/`CrossEncoder` and publish p50/p95/p99 + metrics (Prometheus/OTel).

### Group C — Decommission Old Stack
1. Remove `kryptodb.storage` import from `daemon/app.py` and `kryptodb.memory_api` fallback from `agent_runtime/langgraph/graph.py`.
2. Delete unmounted v0.1.0 modules (`daemon/server.py`, `daemon/mcp_server.py`, `graph/`, `memory_api/`, `agent/`, `integrity/`, `legacy/`) once all tests pass on `core/`.

## 4. Documentation Remediation Plan

| Document | Specific Defects Found | Required Update |
| :--- | :--- | :--- |
| `README.md` | (1) Publishes impossible **`Overall Mean nDCG@10: 1.2273`** (line 126) and unlabeled fake-store `0.44ms` latency (lines 133–134). (2) Claims `SQLite Checkpointer` (lines 38, 78), while live `chat.py` uses volatile `MemorySaver()`. (3) Claims `"Parallel"` vector+BM25 (line 104), while `hybrid_search.py` is sequential. (4) Names vector indexes `entity_vector_index`/`fact_vector_index` (line 102) instead of `entity_embedding_idx`/`fact_embedding_idx`. (5) Claims `"197 Passing"` (line 13) and `"sub-50ms"` without hardware/mock disclosure. (6) Recommends `KRYPTODB_ADMIN_API_KEY=dev-admin-key` (line 198). | Remove `1.2273` nDCG and label all mock/fake-store metrics honestly until real Neo4j benchmarks run; fix index names, checkpointer status, and sequential vs. parallel wording; replace `dev-admin-key` example with a secret-generation command; add a **Known Limitations** section. |
| `docs/ARCHITECTURE.md` | (1) Hardcoded local Windows/WSL links (`file:///mnt/DE94962594960067/...`). (2) Claims `USES` and `DEPLOYED_ON` are `Cardinality.MANY` (line 162), whereas `predicates.py:72,77` defines both as `Cardinality.ONE`. (3) Claims `"completely isolating domain logic from protocols, drivers"` (line 28) despite 11 port leaks in `core/`. (4) Claims `"single choke point"` (line 107) despite `graph.py`, `conflicts.py`, `embedding_retry.py`, and `revoke_fact_tool` bypassing `FilterBuilder`. (5) Claims bi-temporal tracking (line 15) without `recorded_from`/`recorded_to` or `as_of` queries. | Replace all `file://` paths with relative repo links; align predicate cardinality examples with `predicates.py`; regenerate sequence/architecture diagrams from actual `daemon.app:app` call paths; drop `"bi-temporal"` and `"zero-leakage"` claims until exit gates pass. |
| `docs/MIGRATION_MAP.md` | Lists old stack modules as `"Re-export shim"` / `"Parked"`, masking that `agent_runtime/langgraph/graph.py:146` and `daemon/app.py:25` still depend on old modules. | Mark exact remaining call sites and add a deletion checklist for Group C decommissioning. |
| **New Docs to Add** | Missing threat model and ADRs for storage roles, bi-temporal schema, and plaintext vs. encryption decision. | Add `docs/audit/THREAT_MODEL.md`, ADR-0002 (Store Roles: Neo4j/SQLite/Redis), ADR-0003 (Bi-Temporal Schema), and ADR-0004 (Logical Tenant Isolation vs. Encryption). |
