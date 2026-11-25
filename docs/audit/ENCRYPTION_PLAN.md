# KryptoDB Encrypted Memory Plan v3 (`docs/audit/ENCRYPTION_PLAN.md`)

**Consolidated Plan — Date:** 2026-11-24  
**Status:** Supersedes v1 and v2 of `ENCRYPTION_PLAN.md`

## 1. Goal and Scope
* **Goal:** Neo4j never holds readable entity names, predicates, objects, descriptions, event text, or embeddings. A stolen database dump or backup reveals no knowledge content.
* **Decision (Thin Slice First):** Single-process, single-tenant-key, local key file. The `scope` field stays on every fact as the seam for later permissioning.
* **Deferred Until Required:** KMS or Vault, multiple tenants, per-user keys, blind-index key rotation, five-stage live rollout/backfill, and multi-replica daemons.
* **Priority:** The graph RAG core (write path, hybrid read path, multi-hop eval) comes first; encryption must not block it.

## 2. Threat Model

| Actor | In Scope? | Assumption / Control |
| :--- | :---: | :--- |
| Reader of Neo4j dump, backup, tx log, or Redis dump | **Yes** | Must learn no knowledge content. |
| Attacker with Neo4j write access | **Yes (integrity only)** | Must not be able to alter what the agent believes (e.g. flip `superseded` → `active` or replay old ciphertext) without detection. |
| Compromised daemon process or host operator | **No** | Daemon holds keys in RAM. |
| External LLM or embedding API provider | **No** | Sees plaintext; use a local model and local embedder when inference confidentiality is required. |
| Malicious API clients | **Out of scope here** | Handled by the auth/permissioning layer (`authentication.py`, `middleware/context.py`). |

**Accepted Leakage (visible to Neo4j or a dump reader):**
* Graph topology and node degrees.
* Timestamps, `status`, `version`, `confidence`, `trust_weight`, and write cadence.
* Equality and frequency of indexed values (blind indexes and tokens). Truncation is a sizing choice and is not claimed to add ambiguity.
* Access patterns (which blind-index values are queried and which `fact_id`s are fetched).

## 3. What Is Stored Where
* **`:Entity` node:** `id` (random UUID — sole unique key), `scope`, `name_bidx`, `name_ct`, `wrapped_dek`, `kid`, timestamps.
* **`:FACT` edge:** Plaintext routing fields `id`, `scope`, `status`, `version`, `valid_from`, `valid_until`, `recorded_from`, `recorded_to`, `confidence`, `trust_weight`, `subject_bidx`, `predicate_bidx`, `object_bidx`, `search_tokens[]`. Encrypted fields `knowledge_ct` (`subject`, `predicate`, `object`, `description`, `metadata`, `version`, `status`, `valid_from`, `valid_until`, `confidence`, `trust_weight`), `wrapped_dek`, `kid`.
* **`:IdempotencyKey` node:** `scope`, `key` (unique node constraint on Neo4j 5.21 Community), `fact_id`, `created_at`.
* **`:Event` / `:Episode`:** `id`, `session_id`, `scope`, `timestamp`, `event_type` in plaintext; `content_ct`, `summary_ct`, `wrapped_dek`, `kid` encrypted.
* **Embeddings:** Never stored in Neo4j (kept in daemon RAM per scope; rebuilt on startup from decrypted active facts).

## 4. Cryptography
* **Key separation:** Two distinct 256-bit keys loaded from a local key file: `enc_key` (envelope encryption) and `index_key` (HMAC blind indexes). Leaking `index_key` allows dictionary probes against indexes but not decryption.
* **Envelope:** Fresh random 256-bit DEK per fact encrypts the payload (`AES-256-GCM`, 96-bit random nonce). `enc_key` wraps the DEK (`AES-256-GCM`, 96-bit random nonce).
* **Opaque `kid`:** Random identifier (`kid_` + 12 random hex chars), never derived from key bytes. Keyring is `{current_kid, previous_kids}`; reads accept old `kid`s, writes use `current_kid`, and rotation re-wraps DEKs only.
* **AAD:** `kdb:v1:{scope}:{record_id}:{version}` — blocks swapping ciphertext between records and replaying an older version.
* **Normalization:** One pure function `normalize(kind, value)`: Unicode `NFKC`, strip, collapse whitespace, `casefold()`. Predicates also pass through `PredicateRegistry.normalize()`. Used identically on writes and reads.
* **Blind index:** `HMAC_SHA256(index_key, f"{kind}:{normalize(kind, value)}")`, hex truncated to 24 chars (96 bits). Kinds: `entity`, `subject`, `predicate`, `object`, `tok`.
* **Search tokens:** Strip stopwords and punctuation, keep at most 12 tokens/fact in deterministic priority order: subject tokens → object tokens → predicate → longest remaining description tokens (ties broken alphabetically), truncated to 16 hex chars.
* **Language limit:** Whitespace tokenization suits space-delimited scripts; non-space scripts (Chinese, Japanese, Thai) rely on the daemon-side vector path.

## 5. Integrity (Neo4j Writer Defense)
* The encrypted payload stores its own copy of `version`, `status`, `valid_from`, `valid_until`, `confidence`, and `trust_weight`.
* On every decrypt, the daemon recomputes `subject_bidx`, `predicate_bidx`, `object_bidx`, and `search_tokens`, and compares them (along with `version` and `status`) against the edge's stored properties. Any mismatch rejects the record and increments `kryptodb_integrity_failure_total`.
* **Supersession** rewrites the closed edge's encrypted payload with `status='superseded'` and `valid_until=now`, so an old `'active'` ciphertext can never be replayed. **`forget()`** nulls `knowledge_ct` and `wrapped_dek` because forgotten facts are never decrypted again.
* Application logic always uses `confidence` and `trust_weight` from the decrypted payload; plaintext copies exist for Cypher filtering only.

## 6. Retrieval
* **In-RAM vector index (Phase 2):** Per-scope `fact_id -> embedding` index updated on committed writes, supersessions, and forgets; rebuilt at startup by streaming and decrypting active facts. Embedder must be local.
* **Pipeline:** Path A (local semantic query → RAM vector index → top-k `fact_id`s → 1–2 hop Neo4j fetch) fused via RRF with Path B (entity + token blind indexes → 1–2 hop Neo4j traversal with cycle suppression and fan-out cap) → dedupe → decrypt → Section 5 integrity check → exact normalized verification → rerank → return with provenance.
* **Failure handling:** Zero Neo4j candidates returns `not_found`. Any decryption failure on a single-fact read, any missing `kid`, or `> 5%` failures on a batch read raises `DecryptionKeyError` (HTTP `503`) and alerts rather than returning `[]`. Rate-limit and alert on repeated failures to prevent planted-corrupt-record DoS.

## 7. Writes, Supersession & Idempotency
* **Entity resolution:** Look up by `(scope, name_bidx)`, decrypt `name_ct`, reuse entity UUID only on exact normalized match; otherwise create a new `:Entity` with a fresh UUID.
* **Conflict detection:** Query active edges by `(subject_bidx, predicate_bidx)`, decrypt, verify exact `(subject, predicate)`, and capture exact `(id, version)`.
* **Atomic supersession:** Single transaction that acquires write lock on `old_r` (`SET old_r._lock = true`) **before** asserting `old_r.scope = $scope AND old_r.status = 'active' AND old_r.version = $expected_version`, rewrites `old_r` as `superseded`, and creates the new active `:FACT` edge. Zero matched rows raises `SupersessionConflictError` inside the transaction so the entire write rolls back.
* **Idempotency:** `HMAC_SHA256(index_key, f"idem:{scope}:{s}:{p}:{o}:{source_turn_id}")[:32]`, enforced on Neo4j `5.21` Community Edition via a unique `:IdempotencyKey(scope, key)` **node** constraint (relationship uniqueness constraints are Enterprise-only), deleted when the fact is forgotten.
* **Store invariant:** `EncryptedNeo4jMemoryStore` has zero code paths that write plaintext knowledge properties.

## 8. Deletion
* `forget()` is **logical deletion**: after decrypt-and-verify of `(subject, predicate)`, it clears `knowledge_ct`, `wrapped_dek`, all `*_bidx` fields, `search_tokens`, deletes the `:IdempotencyKey` node, sets `status='revoked'`, and removes the fact from the RAM vector index. It does not scrub Neo4j store files, transaction logs, page cache, or historical backups.
* True historical erasure requires destroying `enc_key` (whole-key disaster recovery in v3; per-user keys deferred).

## 9. Plaintext Audit
* A zero-key-access scheduled job scans Neo4j against a **strict property allowlist**: any property outside the schema in Section 3—or any active record missing `knowledge_ct`/`name_ct`—increments `kryptodb_plaintext_violation_total` and alerts. Never seals data automatically.

## 10. Build Order, Release Gates & Open Items
* **Phase 1 (Current):** Key file loader, `normalize`, `BlindIndex`, `EnvelopeCipher`, `EncryptedNeo4jMemoryStore`, Community schema, Section 5 integrity checks, and Phase 1 gate tests.
* **Phase 2:** In-RAM vector index, Path A + Path B retrieval, RRF fusion, and `DecryptionKeyError` failure handling.
* **Phase 3:** `forget()` with `:IdempotencyKey` cleanup and allowlist plaintext audit job.

**Open Items to Verify on Live Stack:**
1. Startup rebuild time and RAM footprint for the largest expected scope.
2. Whether the multi-hop eval set shows the graph beats vector-only (if not, pause).
3. Whether any compliance or customer requirement needs encryption before the core RAG evals are green.
