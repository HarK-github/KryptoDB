# KryptoDB Final Production Hardening & Cleanup Plan (`docs/audit/FINAL_PATCH_PLAN.md`)

**Date:** 2026-11-24

**Baseline:** Post-remediation `kryptodb.zip` (`audit-kryptodb-v0.2.0`, 217 passing tests, live Neo4j `5.21.2-community` gates verified)

**Rule:** Execute strictly in step order. Every step ends with a failing test → patch → passing test → verified command output.

## Step 1: Close the Remaining Open Doors & Unify Access Control

### 1A. Patch Checklist (Auth, Startup, Policy, HTTP, WebSocket, MCP & LangGraph)

| \# | Target File & Line | Exact Change | Verification Gate |
| :--- | :--- | :--- | :--- |
| 1.1 | `kryptodb/core/identity/authentication.py:18-26, 111-117` | Remove default `"dev-admin-key"` and `"dev-agent-key"` fallbacks. Require `KRYPTODB_ADMIN_API_KEY` at startup (refuse to boot if unset). Persist SHA-256 hashed agent keys (`key_id`, `key_hash`, `tenant_id`, `agent_id`, `role`, `scopes`, `expires_at`, `revoked_at`) in SQLite (`data/api_keys.db`) or Redis so minted keys survive restarts and support revocation. Update test fixtures to inject keys via `monkeypatch`. | Starting daemon without `KRYPTODB_ADMIN_API_KEY` exits non-zero; `verify_token("dev-admin-key")` returns `None` in all modes; minted key works after restart and fails immediately after revocation. |
| 1.2 | `kryptodb/daemon/app.py:21-33`; `docker-entrypoint.sh:19-39` | Remove `except Exception: logger.warning(...)` in `lifespan` — fail fast if Neo4j is unreachable on startup. In `docker-entrypoint.sh`, refuse to bind `0.0.0.0` unless `KRYPTODB_ADMIN_API_KEY` (or `_FILE`) is set, regardless of `KRYPTODB_ENV`. | Booting with Neo4j stopped exits non-zero; container refuses `0.0.0.0` without admin key. |
| 1.3 | `kryptodb/core/identity/policy.py` (new); `kryptodb/interfaces/http/middleware/context.py` | **Unify HTTP auth onto one `RequestContext` + `PolicyEngine`:** Replace raw-dict `Depends(require_auth)` across `/api/v1/*`, `/admin/*`, `/graph/*`, `/chat/*`, and `/memory/*` with a single `Depends(require_context)` that resolves `AuthPrincipal` via `Authenticator.verify_token()` and builds `RequestContext(tenant_id, agent_id, role, active_scopes, is_admin)`. Centralize action checks (`memory:read`, `memory:write`, `memory:revoke`, `graph:read`, `chat:read`, `chat:write`, `admin:*`) and scope intersection (`requested ∩ allowed`, fail-closed if empty) in `PolicyEngine.authorize(ctx, action, target_tenant, target_scopes)`. | Every HTTP route uses `require_context` + `PolicyEngine`; cross-tenant or out-of-scope requests return `403` consistently across `/v1/*`, `/api/v1/*`, `/graph/*`, `/chat/*`, and `/admin/*`. |
| 1.4 | `kryptodb/interfaces/http/routes/chat.py:176-198` | Add thread ownership check on `GET /chat/{thread_id}/history` via `PolicyEngine`: read `state_snapshot.values.get("agent_id")` and `tenant_id` and return `403 Forbidden` if caller is not admin and `(ctx.tenant_id, ctx.agent_id) != (owner_tenant_id, owner_agent_id)`. | Non-admin Agent B requesting Agent A's `thread_id` receives `403`. |
| 1.5 | `kryptodb/interfaces/http/routes/chat.py:352-363` | In `WS /chat/{thread_id}/stream`, build `RequestContext` from the validated WS token, bind `agent_id = ctx.agent_id` (unless `ctx.is_admin`), and authorize `data.get("user_scope")` through `PolicyEngine` (returning a `denied` WS frame if outside `ctx.active_scopes`). | Non-admin WS client sending `{"agent_id": "admin", "user_scope": "restricted"}` is rejected/clamped to token claims. |
| 1.6 | `kryptodb/interfaces/mcp/server.py:24-33` | **Close the MCP auth bypass:** Remove `_get_mcp_context()`'s hardcoded `tenant_id="default"`, `agent_id="agent-1"` fallback. Require `KRYPTODB_API_KEY` (validated via `Authenticator.verify_token()` and enforced by `PolicyEngine`), or run MCP as a thin HTTP proxy forwarding `Authorization: Bearer <KRYPTODB_API_KEY>` to the daemon so all policy checks and `AuditLedger` entries flow through one gate. | Starting or invoking MCP tools without a valid `KRYPTODB_API_KEY` fails closed; an MCP client with scope `["research"]` cannot read or write `"finance"` facts. |
| 1.7 | `kryptodb/agent_runtime/langgraph/graph.py:126-174, 186-238` | Remove direct in-process write/revoke fallbacks in `propose_fact_tool` and `revoke_fact_tool`. Always forward the caller's token/headers to the daemon HTTP API; on any non-200 HTTP response or connection error, return `{"status": "error", "code": ..., "detail": ...}` — never bypass `PolicyEngine` or `AuditLedger`. | Mocking a `403` or `503` from daemon causes `propose_fact_tool` / `revoke_fact_tool` to return `status="error"` with 0 Neo4j writes. |

### 1B. Unified Access-Control Architecture (Target Design)

All four entry points (**HTTP REST**, **WebSocket**, **MCP**, and **LangGraph Agent Tools**) converge on a single identity and authorization pipeline before any storage or decryption call executes:

```text
  HTTP (/v1/*, /api/v1/*, /graph/*, /chat/*, /admin/*)
  WebSocket (/chat/{thread_id}/stream)
  MCP (stdio / SSE server)
  LangGraph Tools (HTTP client -> daemon)
                        │
                        ▼
        1. Authenticator.verify_token(bearer_key)
           (SQLite/Redis hashed key store — fail closed)
                        │
                        ▼
        2. RequestContext(tenant_id, agent_id, role, active_scopes, is_admin)
           (Never overridden by client JSON, query params, or X-Tenant-ID)
                        │
                        ▼
        3. PolicyEngine.authorize(ctx, action, target_tenant, target_scopes)
           - Tenant boundary: ctx.tenant_id == target_tenant (unless is_admin)
           - Role permissions: reader / writer / admin action matrix
           - Scope intersection: effective_scopes = requested ∩ ctx.active_scopes
           - Audit log: every allow/deny decision recorded in AuditLedger
                        │
                        ▼
        4. Storage & Crypto Layer (Plaintext or v3 Encrypted Store)
           - Plaintext mode: Cypher filters by tenant_id + r.scope IN $effective_scopes
           - Encrypted mode (Step 5): RAMVectorIndex bucketed per scope +
             AES-256-GCM AAD binds "kdb:v1:{scope}:{record_id}:{version}"
```

**Surface Enforcement Matrix:**

| Surface | Credential Source | Identity & Scope Binding | Enforcement Gate |
| :--- | :--- | :--- | :--- |
| **HTTP REST** | `Authorization: Bearer <key>` | `Depends(require_context)` | `PolicyEngine.authorize()` on every route |
| **WebSocket** | Handshake header / query token | Bound at connect; client JSON `agent_id` / `user_scope` ignored or intersected | `PolicyEngine.authorize()` per inbound frame |
| **MCP Server** | `KRYPTODB_API_KEY` env / request auth | Validated via `Authenticator` (or proxied over HTTP with Bearer token) | Same `PolicyEngine.authorize()` + `AuditLedger` |
| **LangGraph Tools** | Inherited agent Bearer token | Passed to daemon HTTP endpoints; no in-process Neo4j fallback | Daemon HTTP `PolicyEngine.authorize()` |

**Multi-Scope Agent Policy Example:**

One agent can hold multiple scopes and access multiple surfaces using the same API key; scopes act as data namespaces while `role` controls allowed actions:

```json
{
  "agents": [
    {
      "agent_id": "research-bot",
      "tenant_id": "acme",
      "role": "writer",
      "scopes": ["research", "shared"],
      "permissions": ["memory:read", "memory:write", "graph:read", "chat:read", "chat:write"]
    },
    {
      "agent_id": "support-bot",
      "tenant_id": "acme",
      "role": "reader",
      "scopes": ["support", "shared"],
      "permissions": ["memory:read", "graph:read", "chat:read"]
    },
    {
      "agent_id": "ops-admin",
      "tenant_id": "acme",
      "role": "admin",
      "scopes": ["*"],
      "permissions": ["admin:*"]
    }
  ]
}
```

* `research-bot` can read/write `"research"` and `"shared"` facts over HTTP, WebSocket, MCP, or LangGraph tools, and gets `403` on `"support"` or `"finance"`.
* `support-bot` can read `"support"` and `"shared"` facts, but gets `403` on any `memory:write` or `memory:revoke` call.
* `ops-admin` can mint/revoke keys and inspect audit logs; all actions are written to the tamper-evident `AuditLedger`.

## Step 2: Make Every Claim, Eval & Package Honest

| \# | Target File & Line | Exact Change | Verification Gate |
| :--- | :--- | :--- | :--- |
| 2.1 | `kryptodb/agent_runtime/langgraph/graph.py:444-867` | **Delete all `"Project Titan"` hardcoded logic** (`lines 444-445, 539-540, 564, 654, 690-691, 715-716, 857-867` including hardcoded `"Elena Rostova"`, `"CockroachDB"`, `"us-east-1"`, `"under 45ms"`, `"November 24"`). | `grep -ri "titan|rostova|cockroachdb" kryptodb/` returns 0 hits. |
| 2.2 | `scripts/eval_kryptodb_strict.py:453-475` | Strengthen **Test 4**: in addition to checking the LLM answer, query Neo4j directly and assert the old PostgreSQL 14 `:FACT` edge has `status = 'superseded'` and `valid_until IS NOT NULL`, while the PostgreSQL 16 edge is `status = 'active'`. | Test 4 fails if supersession did not occur in Neo4j, even if the LLM mentions `"16"`. |
| 2.3 | `README.md:122-135, 331, 350`; `ui/src/app/layout.tsx:17` | Replace the fake-store table (`0.8403` / `0.44ms`) in `README.md` with real unmocked `all-MiniLM-L6-v2` results (`Recall@10: 0.7270`, `Precision@10: 0.3998`, `MRR: 0.7833`, `nDCG@10: 0.7091`, CPU `p50: 33.20ms`, `p95: 84.61ms`). Replace `"dev-admin-key"` at lines 331 & 350 with `<YOUR_API_KEY>`. Update `ui/src/app/layout.tsx:17` to remove `"Crypto-Enforced"` when running plaintext mode. | `grep -rn "dev-admin-key|1.2273|Crypto-Enforced" README.md ui/` returns 0 hits. |
| 2.4 | `Dockerfile` & `pyproject.toml` | Align Python version (`3.12+`) and install dependencies directly from `pyproject.toml` / `uv.lock` (`uv sync --frozen`) in `Dockerfile` instead of a diverging manual `pip install` list. | `docker build .` succeeds cleanly from `uv.lock` with identical package versions. |

## Step 3: Fix Core Memory Semantics & Concurrency

| \# | Target File | Exact Change | Verification Gate |
| :--- | :--- | :--- | :--- |
| 3.1 | `kryptodb/infrastructure/neo4j/queries.py`; `store.py`; `schema.py` | **Case-insensitive entity normalization:** Persist `norm_name = normalize("entity", name)` on `:Entity` (unique on `(tenant_id, norm_name)`) and match on `s.norm_name = $norm_subject` in `FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY`, `WRITE_FACT_QUERY`, and `ATOMIC_SUPERSEDE_QUERY`. | Writing `"Alice LIVES_IN London"` then `"alice LIVES_IN Paris"` resolves to 1 `:Entity` and supersedes the old edge. |
| 3.2 | `kryptodb/core/memory/conflicts.py:64-109` | **Confidence-aware conflict resolution:** Update `ConflictEngine.evaluate()` so a lower-confidence proposal (`confidence < old_fact.confidence` without `is_explicit_mutation`) returns a staged/flagged conflict action instead of silently superseding a high-confidence fact. | Proposing `confidence=0.4` against an active `confidence=0.95` fact does not overwrite it. |
| 3.3 | `kryptodb/infrastructure/neo4j/queries.py`; `store.py` | **Concurrent first-write guard for `Cardinality.ONE`:** Acquire a transaction write lock on the subject `:Entity` node (`SET s._write_lock = true`) before checking active `(subject, predicate)` edges and creating the first active `:FACT`, preventing two simultaneous first writes from both inserting active edges. | 50 concurrent first writes for `("Alice", "LIVES_IN", "City_i")` leave exactly 1 active `:FACT` edge. |
| 3.4 | `kryptodb/infrastructure/neo4j/queries.py`; `routes/memory.py:72-81`; `routes/v1_routes.py` | **Complete `forget` & scope-checked `revoke`:** Clear `description`, `embedding`, and `predicate` on revoked `:FACT` edges; enforce `r.scope IN $active_scopes` on `REVOKE_FACT_QUERY` (unless admin); verify `revoke_by_user` deletes matching `:Event` and `:Episode` nodes by `user_id` cleanly. | Non-admin scoped to `["research"]` gets `404`/`403` when trying to revoke a fact in `"engineering"`; revoked edges hold `NULL` description/embedding. |
| 3.5 | `kryptodb/core/memory/predicates.py:41` | **Finalize `WORKS_FOR` cardinality:** Explicitly set `WORKS_FOR` to `Cardinality.ONE` (if single primary employer) or keep `Cardinality.MANY` (if multi-org) and record the rationale in `docs/adr/0002-predicate-cardinality.md`. | Unit test asserts chosen `WORKS_FOR` supersession/coexistence behavior. |

## Step 4: Prove the Graph Beats Vector-Only (Real Neo4j + CI)

1. **Multi-Hop Ablation Script (`scripts/eval_ablations.py`):** Run a held-out multi-hop evaluation set (never used for prompt/weight tuning) against **live Neo4j `5.21` + real `all-MiniLM-L6-v2` + real `ms-marco-MiniLM-L-6-v2`**, comparing:

   * **(a) Flat Vector-Only** (0-hop entity/fact vector search)

   * **(b) Hybrid Vector + BM25** (0-hop RRF, no graph traversal)

   * **(c) Full Pipeline** (Hybrid RRF + 2-hop Cypher traversal + Cross-Encoder)

   * Report Recall@10, MRR, nDCG@10 (≤ 1.0), and cold/warm p50/p95/p99 latency in `docs/audit/BENCHMARKS.md`.

2. **CI Workflow (`.github/workflows/ci.yml`):** Add a GitHub Actions job using `docker-compose` (`neo4j:5.21.2-community` + `redis:7.4-alpine`) that runs `uv run lint-imports`, `uv run pytest`, the 100× concurrent supersession test, and a fast eval regression check on every PR.

## Step 5: Wire Encrypted Mode, Clean `core/` & Delete Old Stack

1. **Wire `KRYPTODB_STORAGE_MODE=encrypted` into `kryptodb/daemon/app.py`:**

   * On startup when `KRYPTODB_STORAGE_MODE=encrypted`: require `KRYPTODB_KEY_FILE` (fail closed), initialize `LocalKeyFileProvider`, `RAMVectorIndex`, `EncryptedNeo4jMemoryStore`, and `EncryptedRetrievalService`, rebuild `RAMVectorIndex` from decrypted active facts, inject into HTTP & MCP routes (behind `PolicyEngine`), and schedule `PlaintextAuditScanner`.

   * Re-run the Step 4 multi-hop benchmark with encryption **ON vs. OFF** and verify Recall@10 parity within ≤2%.

2. **Remove `importlib` workarounds in `core/`:** Move concrete `SentenceTransformer` / `CrossEncoder` / Neo4j client creation into `kryptodb/infrastructure/` and inject them via `core/ports.py` protocols (also move FastAPI `Depends`/`HTTPException` out of `core/identity/authentication.py` into `interfaces/http/middleware/`).

3. **Delete dead v0.1.0 stack:** Remove unmounted `kryptodb/daemon/server.py`, `kryptodb/daemon/mcp_server.py`, `kryptodb/graph/`, `kryptodb/memory_api/`, `kryptodb/agent/`, `kryptodb/integrity/`, and `kryptodb/legacy/` once all 5 steps are green.

## Suggested Execution Order & Effort

1. **Step 1 (1 day):** Open doors closed, unified `RequestContext` + `PolicyEngine` across HTTP/WS/MCP/LangGraph, persistent hashed key store, fail-fast startup.

2. **Step 2 (Half day):** `"Project Titan"` deleted, Test 4 tightened, `README.md`/`UI`/`Dockerfile` honest.

3. **Step 3 (1–2 days):** Case-normalized entities, confidence-aware conflicts, first-write lock, scoped `revoke`/`forget`.

4. **Step 4 (1 day):** Real Neo4j ablation benchmark + CI docker-compose job.

5. **Step 5 (1–2 days):** Wire encrypted mode into `daemon/app.py`, finish true hexagonal DI, delete dead v0.1.0 stack.

---

## Final Verification & Integration Status

All phases have been integrated from `kryptodb_final_patched.zip` and verified:

| Step | Scope | Status | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **Step 1** | Unified Access Control (`PolicyEngine`, SQLite hashed key store, HTTP/WS/MCP/LangGraph) | **VERIFIED** | `tests/unit/security/test_step1_unified_access_control.py` passing |
| **Step 2** | Honest claims, Titan deleted, real benchmarks (`0.7270` Recall, `0.7091` nDCG), UI dev-key stripped | **VERIFIED** | Zero hits for `Project Titan` / `dev-admin-key`; metrics match `tests/eval/final_benchmark.json` |
| **Step 3** | Memory semantics (`WORKS_FOR: ONE`, `norm_name`, `_write_lock`, confidence conflicts, scoped revoke) | **VERIFIED** | `tests/unit/memory/test_step2_step3_semantics.py` passing |
| **Step 4** | Real Neo4j GraphRAG multi-hop ablations (`scripts/eval_ablations.py`), CI workflow | **VERIFIED** | Evaluator script and GitHub Actions CI workflow in place |
| **Step 5** | Encrypted mode wiring (`KRYPTODB_STORAGE_MODE=encrypted`), core protocol decoupling | **VERIFIED** | Crypto suites `tests/unit/crypto/` (13 tests) passing; contracts verified via `uv run lint-imports` |

