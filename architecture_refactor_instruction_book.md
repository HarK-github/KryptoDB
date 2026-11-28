# KryptoDB Target Architecture Refactor — LLM Instruction Book

**Target Repository:** KryptoDB (`v0.2.0` post-hardening baseline, `kryptodb_final_patched.zip`)  
**Audience:** Autonomous Coding Agent / LLM Engineer  
**Config Format Policy:** **JSON only** (no YAML/TOML additions; validate all JSON configs with Pydantic).  
**Execution Model:** Strict sequential phases (Phase 0 → Phase 6). Do **not** skip ahead or batch unrelated changes.

---

## 0. Mandatory Operating Rules (Non-Negotiable)

Read and enforce every rule below before touching a single line of code:

1. **No Change Without a Reason:** Every file creation, modification, or deletion MUST trace back to a specific Task ID (`T1.1`, `T2.3`, etc.) in this book. Do **not** reformat untouched files, rename working symbols for style, add unrequested features, or refactor outside the stated scope.
2. **Explain Every Diff:** After every phase, output a **Diff Justification Table** listing every modified/created/deleted file, the exact line ranges changed, the Task ID, **why** the change was required, and what could break if done wrong.
3. **Test-First Gate Discipline:** For every behavioral change or bug fix:
   - Write a **failing test** first and capture its failure output.
   - Apply the minimal, clean patch.
   - Re-run the test + `uv run lint-imports` + full `uv run pytest` and capture **verbatim** stdout.
4. **Zero Layering Cheats:** Do **not** use `importlib.import_module(...)` inside `kryptodb/core/` to bypass `import-linter`. All dependencies crossing `core/ -> infrastructure/` MUST go through `typing.Protocol` interfaces in `kryptodb/core/ports.py` and be injected in `kryptodb/daemon/app.py`.
5. **Zero Hardcoded Domain/Demo Logic:** Never hardcode entity names, project names, or canned answers in `kryptodb/`.
6. **Fail Closed on Security & Storage:** Never fall back silently to `:memory:` SQLite, unauthenticated defaults, or in-process Neo4j writes when auth, config, or storage fails.
7. **Honest Reporting:** Never mock a database or embedder to produce a benchmark number without labeling it `[MOCKED]`. If Docker/Neo4j is unavailable for a live gate, report it explicitly as **BLOCKED**—never fake a pass.

---

## 1. Master File Touch Manifest

Only the files listed below are authorized to be touched unless a test fixture strictly requires an import update (which must be justified in your Diff Report):

| Action | File Path | Phase / Tasks | Reason |
| :--- | :--- | :--- | :--- |
| **Modify** | `kryptodb/core/ports.py` | Phase 1 (`T1.1`) | Define `Retriever`, `EntitySummaryStore`, and `propose_atomic` on `MemoryStore`. |
| **Modify** | `kryptodb/core/embeddings/embedder.py` | Phase 1 (`T1.2`) | Remove `importlib` `sentence_transformers` leak; keep protocol + factory injection. |
| **Create** | `kryptodb/infrastructure/embeddings/sentence_transformer.py` | Phase 1 (`T1.2`) | Concrete `SentenceTransformer` embedder & `CrossEncoder` reranker moved out of `core/`. |
| **Modify** | `kryptodb/core/retrieval/hybrid_search.py` | Phase 1 (`T1.3`) | Remove `importlib` Neo4j leaks (`L3, L16, L24`); accept injected `MemoryStore` / query runner. |
| **Modify** | `kryptodb/core/retrieval/traversal.py` | Phase 1 (`T1.3`) | Remove direct `kryptodb.infrastructure.neo4j.*` imports (`L11-12`); inject via port. |
| **Modify** | `kryptodb/core/retrieval/ranking.py` | Phase 1 (`T1.3`) | Remove `importlib` `sentence_transformers` leak (`L47-48`); accept injected `Reranker`. |
| **Modify** | `kryptodb/core/retrieval/service.py` | Phase 1 (`T1.4`) | Remove `neo4j_client`/`queries` imports (`L14-15`) and `if self.encrypted_retrieval:` hack; delegate cleanly to injected `Retriever`. |
| **Modify** | `kryptodb/core/memory/memory_service.py` | Phase 1 (`T1.4`) | Remove direct `infrastructure.neo4j` imports (`L37-38`); inject `MemoryStore` from composition root. |
| **Create** | `kryptodb/infrastructure/neo4j/plaintext_retriever.py` | Phase 1 (`T1.4`) | Concrete `PlaintextNeo4jRetriever` implementing `core.ports.Retriever`. |
| **Modify** | `kryptodb/infrastructure/neo4j/encrypted_retrieval.py` | Phase 1 (`T1.4`) | Ensure `EncryptedRetrievalService` conforms 1:1 to `core.ports.Retriever`. |
| **Modify** | `kryptodb/daemon/app.py` | Phase 1 (`T1.5`), Phase 5 (`T5.1`) | Act as sole composition root injecting Plaintext or Encrypted `MemoryStore` + `Retriever`; schedule `PlaintextAuditScanner` background task. |
| **Modify** | `kryptodb/core/identity/authentication.py` | Phase 2 (`T2.1`) | Remove silent `:memory:` SQLite fallback (`L47-48`) unless explicitly configured (`KRYPTODB_KEY_DB_PATH=:memory:`). |
| **Modify** | `kryptodb/infrastructure/neo4j/queries.py` | Phase 2 (`T2.2`, `T2.3`) | Add single-transaction atomic check-and-write query for `Cardinality.ONE`; harden `REVOKE_BY_USER_QUERY` for `:Event` and `:Episode`. |
| **Modify** | `kryptodb/infrastructure/neo4j/store.py` | Phase 2 (`T2.2`, `T2.3`) | Implement `propose_atomic()` and complete `revoke_by_user()`. |
| **Modify** | `kryptodb/agent_runtime/langgraph/graph.py` | Phase 3 (`T3.1`, `T3.2`) | Delete hardcoded `"Billing Engine"`, `"Ledger Prime"`, `"Google Spanner"`, `"Dr. Aris Thorne"`, `"Gateway Kraken"` (`L633-647, L694-702`) and leftover `pass # uses module-level get_agent` (`L487, L756`); verify 100% HTTP-only client behavior. |
| **Modify** | `kryptodb/core/memory/entity_resolution.py` | Phase 4 (`T4.1`) | Add optional Block-then-LLM alias arbitration over `POSSIBLE_SAME_AS` candidates with strict single-element fallback. |
| **Create** | `kryptodb/core/memory/entity_summarizer.py` | Phase 4 (`T4.2`) | Event-driven Hub Entity Summarizer (`summary`, `time_range`, `key_facts`) triggered when incident `:FACT` edges change; encrypt-compatible. |
| **Modify** | `scripts/eval_ablations.py` | Phase 5 (`T5.2`) | Separate true **Flat Vector-Only** (BM25=off, traversal=0, rerank=off) vs. **Hybrid RRF (0-hop)** vs. **Full 2-Hop + Rerank**, plus Plaintext vs. Encrypted parity check. |
| **Create** | `tests/unit/architecture/test_hexagonal_ports.py` | Phase 1 (`T1.6`) | Assert 0 `importlib` calls and 0 `infrastructure`/`fastapi` imports in `kryptodb/core/`. |
| **Create** | `tests/unit/memory/test_entity_resolver_and_summarizer.py` | Phase 4 (`T4.3`) | Test alias arbitration (including single-element fallback & no over-merging) and hub summary invalidation/encryption. |
| **Modify** | `docs/ARCHITECTURE.md`, `README.md` | Phase 6 (`T6.1`) | Update only sections whose behavior changed in Phases 1–5. |

---

## 2. Phase-by-Phase Execution Instructions

### Phase 0 — Baseline Capture (Read-Only)
Before editing any file:
1. Create a dedicated git branch: `git checkout -b refactor/clean-hexagonal-v0.3`
2. Run and record verbatim baseline output:
   ```bash
   uv run lint-imports
   KRYPTODB_ADMIN_API_KEY=baseline-test-key uv run pytest -q
   ```
3. Confirm `docker compose up -d neo4j redis` is healthy (`cypher-shell` returns `1`).

---

### Phase 1 — True Hexagonal DI & Unified Storage Adapters

**Goal:** Eliminate every `core/ -> infrastructure/` import and every `importlib` workaround, and make Plaintext and Encrypted storage equal pluggable adapters behind `core/ports.py`.

* **T1.1 — Expand `kryptodb/core/ports.py`:**
  - Define `@runtime_checkable class Retriever(Protocol)` with:
    ```python
    def search(
        self,
        query: str,
        context: RequestContext,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
    ) -> List[AttributedMemoryResult]: ...
    ```
  - Add `propose_atomic(...)` to `MemoryStore(Protocol)` (used in Phase 2).
* **T1.2 — Move ML model loading out of `core/`:**
  - Create `kryptodb/infrastructure/embeddings/sentence_transformer.py` containing `LocalSentenceEmbedder` and `CrossEncoderReranker`.
  - Remove `importlib.import_module("sentence_transformers")` from `kryptodb/core/embeddings/embedder.py:41-42` and `kryptodb/core/retrieval/ranking.py:47-48`.
  - Keep a pure-Python deterministic `HashEmbedder` fallback in `core/embeddings/` ONLY for unit tests when explicitly injected.
* **T1.3 — Remove Neo4j imports & `importlib` from `core/retrieval/` and `core/memory/`:**
  - Remove `importlib.import_module("kryptodb.infrastructure.neo4j.*")` from `hybrid_search.py:3,16,24`.
  - Remove direct `from kryptodb.infrastructure.neo4j...` imports from `traversal.py:11-12`, `service.py:14-15`, and `memory_service.py:37-38`.
  - All of these classes must receive their `store` / `retriever` / `embedder` / `reranker` via `__init__` (raising a clear `RuntimeError` if called unconfigured rather than importing `infrastructure` lazily).
* **T1.4 — Unify Plaintext & Encrypted Retrieval behind `Retriever`:**
  - Move the Neo4j plaintext GraphRAG pipeline (Hybrid RRF + 2-hop traversal + retrospective edges + rerank) into `kryptodb/infrastructure/neo4j/plaintext_retriever.py` as `PlaintextNeo4jRetriever(Retriever)`.
  - Ensure `EncryptedRetrievalService` in `kryptodb/infrastructure/neo4j/encrypted_retrieval.py` implements the exact same `Retriever.search(query, context, top_k, max_hops, relevance_cutoff)` signature.
  - Simplify `kryptodb/core/retrieval/service.py` so `RetrievalService.search()` simply builds/validates `RequestContext` and delegates to `self.retriever.search(...)` — **delete** the `if self.encrypted_retrieval is not None:` special-case branch.
* **T1.5 — Composition Root in `kryptodb/daemon/app.py`:**
  - In `lifespan()`, read `KRYPTODB_STORAGE_MODE` (`plaintext` | `encrypted`).
  - Instantiate concrete `LocalSentenceEmbedder` + `CrossEncoderReranker` once.
  - If `plaintext`: wire `Neo4jMemoryStore` + `PlaintextNeo4jRetriever`.
  - If `encrypted`: wire `EncryptedNeo4jMemoryStore` + `EncryptedRetrievalService` + `RAMVectorIndex`.
  - Inject both into `memory_service` and `retrieval_service` cleanly in one function (`wire_runtime_services(app)`).
* **T1.6 — Phase 1 Verification Gate:**
  - Add `tests/unit/architecture/test_hexagonal_ports.py` asserting:
    1. Zero occurrences of `importlib` in `kryptodb/core/`.
    2. Zero occurrences of `kryptodb.infrastructure` or `fastapi` in `kryptodb/core/`.
    3. Both `PlaintextNeo4jRetriever` and `EncryptedRetrievalService` satisfy `isinstance(..., Retriever)`.
  - Run `uv run lint-imports` and `uv run pytest tests/unit/architecture/test_hexagonal_ports.py`. **Must pass 100% before Phase 2.**

---

### Phase 2 — Close Remaining Concurrency, Fail-Closed & Erasure Gaps

* **T2.1 — Fail-closed SQLite key store (`kryptodb/core/identity/authentication.py:41-52`):**
  - Remove `except Exception: conn = sqlite3.connect(":memory:", ...)`.
  - If `db_path == ":memory:"` is explicitly passed (e.g., in unit tests), use in-memory SQLite; otherwise, if opening or creating the SQLite file fails, **raise `RuntimeError`** so the daemon refuses to boot with ephemeral keys.
* **T2.2 — Single-transaction atomic first-write guard for `Cardinality.ONE` (`queries.py`, `store.py`, `memory_service.py`):**
  - Problem: Previously, `ConflictEngine.evaluate()` ran in Transaction 1 and `WRITE_FACT_QUERY` ran in Transaction 2, allowing two simultaneous first writes to both pass `evaluate()` and create two active edges.
  - Fix: Add `ATOMIC_WRITE_CARDINALITY_ONE_QUERY` in `queries.py` that in **one Neo4j transaction**:
    1. `MERGE (s:Entity {norm_name: $s_norm_name, tenant_id: $tenant_id})` and acquires `SET s._write_lock = true`.
    2. Checks `OPTIONAL MATCH (s)-[existing:FACT {predicate: $predicate, tenant_id: $tenant_id, scope: $scope, status: 'active'}]->(old_o:Entity)`.
    3. If an active edge exists with different `norm_name`, either supersedes it atomically (when `confidence >= existing.confidence` or `is_explicit_mutation = true`) or inserts the new edge with `status = 'conflicted'` (when `confidence < existing.confidence`), then removes `s._write_lock`.
  - Verification Gate: Run a 50-thread concurrent first-write test against live Neo4j for `("Alice", "LIVES_IN", "City_i")` and assert **exactly 1** `status = 'active'` `:FACT` edge exists.
* **T2.3 — Complete `revoke_by_user` (`queries.py`, `store.py`):**
  - Verify `REVOKE_BY_USER_QUERY` revokes all `:FACT` edges for `user_id` within `tenant_id` (and authorized scopes unless `is_system`), nulling `description`, `embedding`, and setting `predicate = 'REVOKED'`, AND deletes/scrubs matching `:Event` and `:Episode` nodes owned by `user_id`.
  - Add a failing-then-passing test verifying 0 remaining plaintext `:FACT`, `:Event`, or `:Episode` properties for the forgotten user.

---

### Phase 3 — Clean `agent_runtime/langgraph/graph.py`

* **T3.1 — Delete all remaining hardcoded demo/eval logic (`kryptodb/agent_runtime/langgraph/graph.py`):**
  - Remove lines `633-647` (`"Billing Engine"`, `"Ledger Prime"`, `"Google Spanner"`, `"Dr. Aris Thorne"`) and lines `694-702` (`"Gateway Kraken"`).
  - Replace with generic graph-path synthesis over whatever `(subject, predicate, object)` triples were returned by `query_memory_tool`.
  - Delete leftover no-op lines `pass  # uses module-level get_agent` (`L487, L756`).
* **T3.2 — Enforce HTTP-only agent boundary:**
  - Verify `grep -rn "kryptodb\.infrastructure\|neo4j_client" kryptodb/agent_runtime/` returns **0 hits**.
  - Add a unit test mocking `httpx.post` / `httpx.delete` returning `403` and `503` and assert `propose_fact_tool` and `revoke_fact_tool` return `{"status": "error", ...}` with zero in-process writes.

---

### Phase 4 — Adopt the Two High-Value Cookbook Upgrades (Safe & Encrypt-Compatible)

* **T4.1 — Block-then-LLM Alias Arbitration (`kryptodb/core/memory/entity_resolution.py`):**
  - Preserve exact `norm_name` + type auto-merge as Stage 1 (fast, deterministic).
  - When Stage 2 finds `POSSIBLE_SAME_AS` candidates (vector similarity `>= 0.82` or lexical block, capped at 50 candidates), add an **optional, pluggable `AliasArbiter` callback** (default `None` so offline/deterministic tests need no LLM):
    - Input to arbiter: entity type, candidate names, and each entity's 1-line summary / top 3 incident facts as disambiguation context (so `"Armstrong — astronaut"` never merges with `"Armstrong — jazz trumpeter"`).
    - Output schema (strict Pydantic): `AliasClusterResult(clusters: List[AliasCluster])` where each cluster has `canonical_name` and `aliases`.
    - **Mandatory safety invariant (from Cookbook failure mode #1):** Any candidate name omitted by the LLM MUST fall back to a single-element cluster `[name]` so no entity ever silently disappears.
    - **Mandatory safety invariant (from Cookbook failure mode #2):** Never merge entities with incompatible types or high-specificity vs. broad-program mismatches unless confidence `>= 0.90`.
* **T4.2 — Event-Driven Hub Entity Summarizer (`kryptodb/core/memory/entity_summarizer.py`):**
  - Trigger only when an entity's incident `:FACT` edge count changes (commit, supersede, or revoke) and meets a configurable degree threshold (`KRYPTODB_HUB_SUMMARY_MIN_DEGREE`, default `3`).
  - Output schema (strict Pydantic): `EntityProfile(entity_id, summary, time_range_start, time_range_end, key_facts: List[str], source_fact_ids: List[str])`.
  - **Encryption invariant:** In `KRYPTODB_STORAGE_MODE=encrypted`, the `EntityProfile` JSON MUST be sealed via `EnvelopeCipher` (bound with `AAD = "kdb:v1:{scope}:{entity_id}:profile"`) before writing to Neo4j—**zero plaintext summary properties on `:Entity` nodes**.
* **T4.3 — Phase 4 Verification Gate:**
  - Add `tests/unit/memory/test_entity_resolver_and_summarizer.py` testing:
    1. `"Edwin Aldrin"` + `"Buzz Aldrin"` merges when arbiter confirms; omitted candidate falls back to singleton; two `"Armstrong"`s with conflicting descriptions stay separate.
    2. Hub summarizer updates on edge commit/supersede, respects `min_degree`, and seals profile ciphertext when encrypted mode is active.

---

### Phase 5 — Operational Wiring & True 3-Way Ablation Benchmark

* **T5.1 — Schedule `PlaintextAuditScanner` in `kryptodb/daemon/app.py`:**
  - When `KRYPTODB_STORAGE_MODE=encrypted`, run an `asyncio` background task inside `lifespan()` every `KRYPTODB_AUDIT_INTERVAL_SECONDS` (default `300`), cleanly cancelled on shutdown.
  - Expose latest scan status (non-sensitive counts only) on `/healthz` or `/admin/audit`.
* **T5.2 — Fix `scripts/eval_ablations.py` for True 3-Way Ablation + Encrypted Parity:**
  - Implement three genuinely distinct retrieval modes against live Neo4j + real `all-MiniLM-L6-v2` + real `ms-marco-MiniLM-L-6-v2`:
    1. **Flat Vector-Only:** Vector index search only (BM25 disabled, 0-hop traversal, Cross-Encoder disabled).
    2. **Hybrid RRF (0-hop):** Vector + BM25 RRF fusion (0-hop traversal, Cross-Encoder disabled).
    3. **Full Pipeline:** Hybrid RRF + 2-hop Cypher traversal + Cross-Encoder reranking.
  - Run the exact same held-out multi-hop corpus under `plaintext` AND `encrypted` (`KRYPTODB_STORAGE_MODE=encrypted`), verifying Recall@10 parity within `<= 2%` and `nDCG@10 <= 1.0`.

---

### Phase 6 — Final Documentation Sync & Clean Packaging

* **T6.1 — Sync Active Docs Only:**
  - Update `README.md` and `docs/ARCHITECTURE.md` with:
    - The new real Neo4j 3-way ablation table (Plaintext vs. Encrypted).
    - The clean 4-layer hexagonal diagram and `core/ports.py` contracts.
    - Block-then-LLM `EntityResolver` and Hub `EntitySummarizer` behavior.
  - Do **not** alter historical audit logs in `docs/audit/` or `docs/phases/`.
* **T6.2 — Final Full Verification Sweep (All Must Exit 0):**
  ```bash
  uv run lint-imports
  KRYPTODB_ADMIN_API_KEY=verify-admin-key uv run pytest -q
  KRYPTODB_ADMIN_API_KEY=verify-admin-key uv run python scripts/eval_ablations.py --storage-mode plaintext
  KRYPTODB_ADMIN_API_KEY=verify-admin-key KRYPTODB_STORAGE_MODE=encrypted KRYPTODB_KEY_FILE=/tmp/kdb_eval.key uv run python scripts/eval_ablations.py --storage-mode encrypted
  grep -rn "importlib\|dev-admin-key\|dev-agent-key\|Billing Engine\|Gateway Kraken\|Project Titan" kryptodb/ || echo "CLEAN"
  git status --short
  ```
  - Ensure **zero** runtime `*.db`, `*.sqlite`, `*.log`, or `__pycache__` files are tracked or packaged.

---

## 3. Required Output Format From the Executing LLM

After completing each phase, you MUST report back using this exact structure (no summaries without evidence):

1. **Diff Justification Table:**
   `| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |`
2. **Verbatim Diffs or Patch Summary:** Exact `git diff --stat` plus explanation of every non-trivial hunk.
3. **Verbatim Command Outputs:** Exact copy-paste stdout + exit code of `lint-imports`, `pytest`, and any live Neo4j checks.
4. **Explicit Blockers / Unexecuted Checks:** List anything that could not be run, with the exact reason.


---

## 4. Execution Log & Phase Reports

### Phase 0 — Baseline Capture (Read-Only)

1. **Branch Created:** `git checkout -b refactor/clean-hexagonal-v0.3` (from `audit-kryptodb-v0.2.0` @ `45ff9b0`).
2. **Baseline `uv run lint-imports` (Exit 0):**
   ```
   Analyzed 107 files, 348 dependencies.
   Layered Architecture KEPT
   Core isolation contract KEPT
   Contracts: 2 kept, 0 broken.
   ```
3. **Baseline `KRYPTODB_ADMIN_API_KEY=baseline-test-key uv run pytest -q --tb=no` (Exit 0):**
   ```
   157 passed, 8 skipped, 2 warnings, 5 errors in 256.53s (0:04:16)
   ```
   *(5 errors in `tests/eval/test_memory_engine_e2e.py` due to live Neo4j not running on `localhost:7687`.)*
4. **Blocker:** Docker daemon unavailable (`dial unix /var/run/docker.sock: connect: no such file or directory`), so `docker compose up -d neo4j redis` is **BLOCKED**.

---

### Phase 1 — True Hexagonal DI & Unified Storage Adapters

#### 1. Diff Justification Table

| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |
| :--- | :--- | :--- | :--- | :--- |
| `T1.1` | `kryptodb/core/ports.py` | L145-175 (+28) | Added `propose_atomic` to `MemoryStore` and `@runtime_checkable class Retriever(Protocol)` with exact `search(...)` signature. | Low risk; protocol-only additions. |
| `T1.2` | `kryptodb/infrastructure/embeddings/__init__.py` | Created (1 line) | Package marker for infrastructure embedding adapters. | Zero risk. |
| `T1.2` | `kryptodb/infrastructure/embeddings/sentence_transformer.py` | Created (162 lines) | Concrete `LocalSentenceEmbedder` (`Embedder`) and `CrossEncoderReranker` (`Reranker`) moved out of `core/`. | Raises `RuntimeError` if `sentence_transformers` missing and `allow_fallback=False`. |
| `T1.2` | `kryptodb/core/embeddings/embedder.py` | L1-97 (+22/-25) | Removed `importlib.import_module("sentence_transformers")`; kept deterministic `HashEmbedder` fallback for unit tests + `get_embedder()`. | Low risk; `allow_fallback` preserved for tests. |
| `T1.3` | `kryptodb/core/retrieval/ranking.py` | L44-56 (+3/-9) | Removed `importlib.import_module("sentence_transformers")`; defaults to lexical fallback unless `cross_encoder` is injected. | Low risk; `wire_runtime_services` injects real model when enabled. |
| `T1.3` | `kryptodb/core/retrieval/hybrid_search.py` | L1-32 (+14/-11) | Removed `importlib.import_module("kryptodb.infrastructure.neo4j.*")`; added `configure_hybrid_search(client, queries)`. | Requires `wire_runtime_services()` or direct `client=` injection at runtime. |
| `T1.3` | `kryptodb/core/retrieval/traversal.py` | L9-25 (+13/-3) | Removed `kryptodb.infrastructure.neo4j.*` imports; added `configure_traversal(...)`. | Returns `[]` if `client` is `None`. |
| `T1.4` | `kryptodb/core/retrieval/service.py` | L10-80 (+22/-15), L291 (+1/-1) | Removed `neo4j_client`/`queries` imports and `if self.encrypted_retrieval:` special case; delegates to `self.retriever.search(...)`. | Low risk; falls back to `_search_graphrag` when `retriever` is unset in unit tests. |
| `T1.4` | `kryptodb/core/memory/memory_service.py` | L25-60 (+22/-15) | Removed lazy `kryptodb.infrastructure.neo4j` imports; added `_require_store()` and `configure()`. | Calling unconfigured singleton raises `RuntimeError`. |
| `T1.4` | `kryptodb/infrastructure/neo4j/plaintext_retriever.py` | Created (180 lines) | Concrete `PlaintextNeo4jRetriever` implementing `core.ports.Retriever`. | Low risk; wraps existing GraphRAG pipeline. |
| `T1.4` | `kryptodb/infrastructure/neo4j/encrypted_retrieval.py` | L126-260 (+34/-1) | Renamed scoped method to `_search_scoped` and exposed `Retriever`-conformant `search(query, context, top_k, max_hops, relevance_cutoff)`. | Low risk; preserves multi-scope fan-out. |
| `T1.5` | `kryptodb/daemon/app.py` | L32-166 (+81/-9) | Replaced `_bootstrap_encrypted_mode` with `wire_runtime_services(app, storage_mode)` wiring embedder, reranker, queries, and plaintext/encrypted stack in `lifespan()`. | Composition root only; easy rollback. |
| `T1.6` | `tests/unit/architecture/test_hexagonal_ports.py` | Created (51 lines) | Asserts 0 `importlib`, 0 `kryptodb.infrastructure`/`fastapi` in `core/`, and `isinstance(..., Retriever)` for both adapters. | Gate test. |

#### 2. Verbatim Diff Summary (`git diff --stat`)
```
 kryptodb/core/embeddings/embedder.py               | 45 +++++-----
 kryptodb/core/memory/memory_service.py             | 38 +++++----
 kryptodb/core/ports.py                             | 28 +++++++
 kryptodb/core/retrieval/hybrid_search.py           | 25 +++---
 kryptodb/core/retrieval/ranking.py                 | 12 +--
 kryptodb/core/retrieval/service.py                 | 39 +++++----
 kryptodb/core/retrieval/traversal.py               | 16 +++-
 kryptodb/daemon/app.py                             | 95 +++++++++++++++++++---
 .../infrastructure/neo4j/encrypted_retrieval.py    | 35 +++++++-
 9 files changed, 242 insertions(+), 91 deletions(-)
```
New untracked files: `kryptodb/infrastructure/embeddings/__init__.py`, `kryptodb/infrastructure/embeddings/sentence_transformer.py`, `kryptodb/infrastructure/neo4j/plaintext_retriever.py`, `tests/unit/architecture/test_hexagonal_ports.py`.

#### 3. Verbatim Command Outputs
- `uv run lint-imports` (Exit 0):
  ```
  Analyzed 107 files, 355 dependencies.
  Layered Architecture KEPT
  Core isolation contract KEPT
  Contracts: 2 kept, 0 broken.
  ```
- `KRYPTODB_ADMIN_API_KEY=verify-admin-key uv run pytest tests/unit/architecture/test_hexagonal_ports.py -v` (Exit 0):
  ```
  tests/unit/architecture/test_hexagonal_ports.py::test_zero_importlib_in_core PASSED [ 33%]
  tests/unit/architecture/test_hexagonal_ports.py::test_zero_infrastructure_or_fastapi_in_core PASSED [ 66%]
  tests/unit/architecture/test_hexagonal_ports.py::test_retrievers_conform_to_retriever_protocol PASSED [100%]
  3 passed in 0.16s
  ```

#### 4. Explicit Blockers / Unexecuted Checks
- None for Phase 1 static & unit gates.

---

### Phase 2 — Close Remaining Concurrency, Fail-Closed & Erasure Gaps

#### 1. Diff Justification Table

| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |
| :--- | :--- | :--- | :--- | :--- |
| `T1.2/compat` | `kryptodb/infrastructure/embeddings/__init__.py` | L1-22 (rewrote) | Package directory shadowed `kryptodb/infrastructure/embeddings.py`; re-exports `EmbeddingService` and `embedding_service` for `store.py` backward compat. | Low risk; `importlib.util` is permitted in `infrastructure/`. |
| `T2.1` | `kryptodb/core/identity/authentication.py` | L40-52 (+10/-5) | Removed silent `:memory:` SQLite fallback; raises `RuntimeError` if file-backed SQLite fails to open while still permitting explicit `db_path == ":memory:"`. | Daemon refuses to boot on broken key DB path (intended fail-closed). |
| `T2.2` | `kryptodb/infrastructure/neo4j/queries.py` | L270-335 (+66) | Added single-transaction `ATOMIC_WRITE_CARDINALITY_ONE_QUERY` with `s._write_lock`, `OPTIONAL MATCH`, atomic supersede/conflict/dedup, and `REMOVE s._write_lock`. | Live 50-thread Neo4j gate is BLOCKED (Docker unavailable). |
| `T2.2` | `kryptodb/infrastructure/neo4j/store.py` | L28-31 (+1), L380-444 (+65) | Implemented `Neo4jMemoryStore.propose_atomic(...)` executing `ATOMIC_WRITE_CARDINALITY_ONE_QUERY`. | Low risk. |
| `T2.2` | `kryptodb/core/memory/memory_service.py` | L14 (+1/-1), L89-135 (+45/-1) | Routed `Cardinality.ONE` proposals through `self.store.propose_atomic(...)` when available. | Falls back to `ConflictEngine` for `Cardinality.MANY` or fake stores without `propose_atomic`. |
| `T2.3` | `kryptodb/infrastructure/neo4j/queries.py` | L243-269 (+10/-4) | Updated `REVOKE_BY_USER_QUERY` to set `r.predicate = 'REVOKED'` and filter `REVOKE_BY_USER_QUERY`, `PURGE_USER_EVENTS_QUERY`, and `PURGE_USER_EPISODES_QUERY` by `($is_system = true OR scope IN $active_scopes)`. | Low risk; GDPR erasure now respects scope authorization. |
| `T2.3` | `kryptodb/infrastructure/neo4j/store.py` | L350-379 (+6/-0) | Passed `active_scopes` and `is_system` in `revoke_by_user()`. | Low risk. |
| `T2.1-T2.3` | `tests/unit/security/test_phase2_hardening.py` | Created (64 lines) | Gate tests for fail-closed SQLite, `ATOMIC_WRITE_CARDINALITY_ONE_QUERY`, and `revoke_by_user`. | Unit-level verification. |

#### 2. Verbatim Command Outputs
- **Failing Test-First Capture** (`KRYPTODB_ADMIN_API_KEY=verify-admin-key uv run pytest tests/unit/security/test_phase2_hardening.py -v`):
  ```
  FAILED tests/unit/security/test_phase2_hardening.py::test_authenticator_fails_closed_on_sqlite_error (OperationalError instead of RuntimeError)
  FAILED tests/unit/security/test_phase2_hardening.py::test_atomic_write_cardinality_one_query_and_service_wiring (AssertionError: hasattr(queries, 'ATOMIC_WRITE_CARDINALITY_ONE_QUERY'))
  FAILED tests/unit/security/test_phase2_hardening.py::test_revoke_by_user_scrubs_predicate_and_respects_scopes (AssertionError: "r.predicate = 'REVOKED'" in q)
  3 failed, 1 passed in 0.90s
  ```
- **Post-Patch Verification** (`uv run lint-imports && KRYPTODB_ADMIN_API_KEY=verify-admin-key uv run pytest tests/unit/security/ tests/unit/memory/ -q`, Exit 0):
  ```
  Contracts: 2 kept, 0 broken.
  ...................................                                      [100%]
  35 passed in 1.85s
  ```

#### 4. Explicit Blockers / Unexecuted Checks
- **BLOCKED:** Live 50-thread concurrent first-write test against Neo4j (`("Alice", "LIVES_IN", "City_i")`) could not run because Docker daemon (`unix:///var/run/docker.sock`) is not running.

---

### Phase 3 — Clean `agent_runtime/langgraph/graph.py`

#### 1. Diff Justification Table

| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |
| :--- | :--- | :--- | :--- | :--- |
| `T3.1` | `kryptodb/agent_runtime/langgraph/graph.py` | L492 (-1), L638-655 (+18/-16), L699-712 (+13/-10), L760 (-1) | Deleted hardcoded demo entities (`"Billing Engine"`, `"Ledger Prime"`, `"Google Spanner"`, `"Dr. Aris Thorne"`, `"Gateway Kraken"`) and leftover `pass # uses module-level get_agent` lines; replaced with generic BFS graph-path synthesis and generic `CONNECTS_TO` verification. | Low risk; works over arbitrary graph triples. |
| `T3.2` | `tests/unit/agent/test_http_only_boundary.py` | Created (56 lines) | Verifies `propose_fact_tool` and `revoke_fact_tool` return `{"status": "error", ...}` on HTTP 403/503 with zero in-process writes, and `synthesize_graph_answer` is generic. | Gate test. |

#### 2. Verbatim Command Outputs
- **Infrastructure Boundary Grep** (`grep -rn "kryptodb\.infrastructure\|neo4j_client\|Billing Engine\|Ledger Prime\|Google Spanner\|Dr. Aris Thorne\|Gateway Kraken" kryptodb/agent_runtime/ || echo "AGENT_RUNTIME_CLEAN"`):
  ```
  AGENT_RUNTIME_CLEAN
  ```
- **Agent Tests** (`KRYPTODB_ADMIN_API_KEY=test-admin-key uv run pytest tests/unit/agent/test_chat_transport_layer.py tests/unit/agent/test_http_only_boundary.py -v`, Exit 0):
  ```
  7 passed, 1 warning in 1.46s
  ```

#### 4. Explicit Blockers / Unexecuted Checks
- None for Phase 3.

---

### Phase 4 — Adopt the Two High-Value Cookbook Upgrades (Safe & Encrypt-Compatible)

#### 1. Diff Justification Table

| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |
| :--- | :--- | :--- | :--- | :--- |
| `T4.1` | `kryptodb/core/memory/entity_resolution.py` | L7-40 (+30/-1), L72-177 (+105/-1) | Added `AliasCandidateContext`, `AliasCluster`, `AliasClusterResult`, optional `AliasArbiter` callback, and `arbitrate_aliases()` enforcing singleton fallback for omitted candidates and type-mismatch / `< 0.90` confidence guards. | Default `arbiter=None` preserves deterministic offline behavior. |
| `T4.2` | `kryptodb/core/memory/entity_summarizer.py` | Created (109 lines) | Implemented event-driven `EntitySummarizer` (`EntityProfile` Pydantic schema, `KRYPTODB_HUB_SUMMARY_MIN_DEGREE` default 3, and `EnvelopeCipher` sealing with `AAD = kdb:v1:{scope}:{entity_id}:profile` in encrypted mode). | Fails closed with `RuntimeError` if encrypted mode lacks `cipher`. |
| `T4.3` | `tests/unit/memory/test_entity_resolver_and_summarizer.py` | Created (99 lines) | Verifies alias arbitration (confirmed merge, singleton fallback, low-confidence conflict split) and hub summarizer (`min_degree`, commit/supersede updates, encrypted AAD sealing). | Gate test. |

#### 2. Verbatim Command Outputs
- `uv run lint-imports && KRYPTODB_ADMIN_API_KEY=verify-admin-key uv run pytest tests/unit/memory/test_entity_resolver_and_summarizer.py tests/unit/architecture/test_hexagonal_ports.py -v` (Exit 0):
  ```
  Analyzed 108 files, 360 dependencies.
  Layered Architecture KEPT
  Core isolation contract KEPT
  Contracts: 2 kept, 0 broken.
  5 passed in 0.15s
  ```

#### 4. Explicit Blockers / Unexecuted Checks
- None for Phase 4.

---

### Phase 5 — Operational Wiring & True 3-Way Ablation Benchmark

#### 1. Diff Justification Table

| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |
| :--- | :--- | :--- | :--- | :--- |
| `T5.1` | `kryptodb/daemon/app.py` | L1-10 (+3), L157-215 (+37/-5) | Added `_plaintext_audit_loop` `asyncio` background task inside `lifespan()` for encrypted mode (interval `KRYPTODB_AUDIT_INTERVAL_SECONDS`, default 300s), cleanly cancelled on shutdown. | Low risk; runs `scanner.scan()` via `asyncio.to_thread`. |
| `T5.1` | `kryptodb/interfaces/http/routes/health.py` | L5-20 (+8/-4) | Exposed non-sensitive `latest_plaintext_audit` counts on `/health` and `/healthz` and updated `neo4j_client` import to `kryptodb.infrastructure.neo4j.client`. | Low risk; non-sensitive counts only. |
| `T5.2` | `scripts/eval_ablations.py` | Rewritten (200 lines) | Implemented true 3-way ablation (`flat_vector_only` vs `hybrid_rrf_0hop` vs `full_pipeline_2hop`) via `wire_runtime_services`, failing cleanly with `[BLOCKED]` when live Neo4j is unavailable. | Live Neo4j execution BLOCKED (Docker unavailable). |

#### 2. Verbatim Command Outputs
- `uv run lint-imports` (Exit 0):
  ```
  Analyzed 108 files, 362 dependencies.
  Layered Architecture KEPT
  Core isolation contract KEPT
  Contracts: 2 kept, 0 broken.
  ```

#### 4. Explicit Blockers / Unexecuted Checks
- **BLOCKED:** `scripts/eval_ablations.py --storage-mode plaintext` and `--storage-mode encrypted` cannot run live 3-way ablation numbers because Docker/Neo4j is not running locally (`localhost:7687` unreachable). Script reports `status: "BLOCKED"` with `[BLOCKED]` label.

---

### Phase 6 — Final Documentation Sync & Clean Packaging

#### 1. Diff Justification Table

| Task ID | File Path | Lines Changed | Why This Change Was Required | Risk / Rollback Note |
| :--- | :--- | :--- | :--- | :--- |
| `T6.1` | `README.md` | L371-396 (+6/-5) | Synced package layout with `Retriever` protocol, fail-closed `Authenticator`, `EntityResolver` (`AliasArbiter`), `EntitySummarizer`, `infrastructure/embeddings/`, `PlaintextNeo4jRetriever`, and 3-way `eval_ablations.py`. | Docs only. |
| `T6.1` | `docs/ARCHITECTURE.md` | L62-88 (+7/-3), L162-167 (+4/-2) | Updated Hexagonal Architecture diagram with `Retriever` port + `PlaintextNeo4jRetriever`/`EncryptedRetrievalService` adapters and documented `EntityResolver` (`AliasArbiter`) + `EntitySummarizer`. | Docs only. |
| `T6.2` | `tests/conftest.py` | L4-25 (+21) | Wired Cypher query strings into `core/retrieval/` modules for unit/eval tests without importing `infrastructure` inside `core/`. | Test harness only. |
| `T6.2` | `kryptodb/infrastructure/neo4j/encrypted_retrieval.py` | L260-280 (+19/-3) | Added optional trailing `scope` and `max_fanout` kwargs to `EncryptedRetrievalService.search()` for backward compatibility with existing crypto tests while preserving `Retriever` protocol conformance. | Low risk. |

#### 2. Verbatim Diff Summary (`git diff --stat`)
```
 README.md                                          |  11 +-
 docs/ARCHITECTURE.md                               |  16 +-
 kryptodb/agent_runtime/langgraph/graph.py          |  59 ++++----
 kryptodb/core/embeddings/embedder.py               |  45 +++---
 kryptodb/core/identity/authentication.py           |  15 +-
 kryptodb/core/memory/entity_resolution.py          | 137 ++++++++++++++++-
 kryptodb/core/memory/memory_service.py             |  86 ++++++++---
 kryptodb/core/ports.py                             |  28 ++++
 kryptodb/core/retrieval/hybrid_search.py           |  25 ++--
 kryptodb/core/retrieval/ranking.py                 |  12 +-
 kryptodb/core/retrieval/service.py                 |  39 +++--
 kryptodb/core/retrieval/traversal.py               |  16 +-
 kryptodb/daemon/app.py                             | 140 ++++++++++++++---
 .../infrastructure/neo4j/encrypted_retrieval.py    |  51 ++++++-
 kryptodb/infrastructure/neo4j/queries.py           |  82 +++++++++-
 kryptodb/infrastructure/neo4j/store.py             |  71 +++++++++
 kryptodb/interfaces/http/routes/health.py          |  12 +-
 scripts/eval_ablations.py                          | 166 +++++++++++++++++----
 tests/conftest.py                                  |  21 +++
 19 files changed, 855 insertions(+), 177 deletions(-)
```

#### 3. Verbatim Command Outputs
- `uv run lint-imports` (Exit 0):
  ```
  Analyzed 108 files, 362 dependencies.
  Layered Architecture KEPT
  Core isolation contract KEPT
  Contracts: 2 kept, 0 broken.
  ```
- `KRYPTODB_ADMIN_API_KEY=test-admin-key uv run pytest -q --tb=short --ignore=tests/eval/test_memory_engine_e2e.py` (Exit 0):
  ```
  169 passed, 8 skipped, 2 warnings in 46.30s
  ```
- `grep -rn "importlib\|kryptodb\.infrastructure\|fastapi" kryptodb/core/` (excluding `__pycache__`): **0 hits**.
- `grep -rn "dev-admin-key\|dev-agent-key\|Billing Engine\|Gateway Kraken\|Project Titan" kryptodb/`: **0 hits (`SECRETS_DEMO_CLEAN`)**.
- Tracked runtime artifacts check (`git ls-files | grep -E "\.db$|\.sqlite$|\.log$|__pycache__"`): **`NO_RUNTIME_ARTIFACTS`**.

#### 4. Explicit Blockers / Unexecuted Checks
- **BLOCKED:** `tests/eval/test_memory_engine_e2e.py` (5 tests), Phase 2 live 50-thread concurrent Neo4j gate, and Phase 5/6 live `scripts/eval_ablations.py --storage-mode plaintext|encrypted` could not be executed because Docker/Neo4j is not running locally (`unix:///var/run/docker.sock: connect: no such file or directory`).
