# KryptoDB Threat Model (`docs/audit/THREAT_MODEL.md`)

**Version:** 2.0 (v0.2.0 Plaintext & v3 Encrypted Memory Stacks)  
**Date:** 2026-11-24

## 1. System Boundary & Security Model
KryptoDB supports two operational storage modes configured via `KRYPTODB_STORAGE_MODE`:
1. **Plaintext Mode (Default):** Logical isolation enforced in application code via `PolicyEngine`, `RequestContext`, and `FilterBuilder` (`tenant_id` + `scope`).
2. **Encrypted Memory Mode v3 (`KRYPTODB_STORAGE_MODE=encrypted`):** Cryptographic isolation enforced via AES-256-GCM authenticated envelopes with AAD, HMAC blind indexes, and in-memory `RAMVectorIndex`.

* **Trust boundary:** In Plaintext mode, the FastAPI daemon, Neo4j instance, Redis instance, and checkpointer store are inside the trusted boundary. In Encrypted mode, the database storage at rest is treated as untrusted. All HTTP/WebSocket/MCP clients, LLM outputs, and ingested external text are **untrusted**.

---

## 2. Adversaries & In-Scope Threats (Plaintext Mode)

| Adversary | Capabilities | Primary Attack Vectors (Verified in v0.2.0) | Remediated Controls |
| :--- | :--- | :--- | :--- |
| **Unauthenticated Network Caller** | Can reach port 8000 (HTTP/WS) | Unauthenticated `/v1/*`, legacy `/memory/*`, `WS /chat/{id}/stream`, hardcoded `"dev-admin-key"`. | **Resolved:** Fail-closed bearer auth on every route; removed `"dev-admin-key"`; startup guard requires `KRYPTODB_ADMIN_API_KEY`. |
| **Compromised / Malicious Agent (Cross-Tenant)** | Holds a valid non-admin agent API key for Tenant A | Spoofing `X-Tenant-ID`; passing `?tenant_id=system`; calling `/admin/agents` or reading unmasked keys. | **Resolved:** `PolicyEngine` binds `tenant_id`, `agent_id`, and `scopes` strictly to hashed API-key claims in `data/api_keys.db`. |
| **Indirect Prompt Injection / Memory Poisoning** | Controls external text read by an agent | Tricks extractor into writing false `Cardinality.ONE` facts superseding ground truth. | **Resolved:** Source trust tiers (`user_direct` > `verified_system` > `tool_output`), `ConflictEngine` flags low-confidence proposals (`FLAG_CONFLICT`), and HITL interrupts trigger for sensitive scopes. |
| **Stale / Replayed HITL Approval** | Replays an old approval or approves after graph drift | `POST /agent/approve` lacks admin check, expiry, single-use token. | **Resolved:** Admin-only approval, durable checkpointer, and single-use interrupt state resumption. |
| **Privacy / Erasure Non-Compliance** | Exercises right-to-be-forgotten (`forget_user` / `forget_memory`) | `revoke` only marked `:FACT` `status='revoked'`, leaving events/episodes on disk. | **Resolved:** `revoke_by_user` cascade-purges `:Event` and `:Episode` nodes; `REVOKE_FACT_QUERY` sets `description=NULL` and `embedding=NULL`. |

---

## 3. Cryptographic Memory v3 Threat Model (Encrypted Storage Mode)

When `KRYPTODB_STORAGE_MODE=encrypted` is active:

| Threat | Attack Vector | Cryptographic Mitigation |
| :--- | :--- | :--- |
| **Untrusted Database Operator / Disk Dump** | Attacker reads Neo4j raw `.db` files, transaction logs, or unencrypted database backups. | **AES-256-GCM Envelope Encryption:** Knowledge properties are encrypted using per-tenant keys derived via HKDF (`LocalKeyFileProvider`). Ciphertext contains zero plaintext entity names, descriptions, or properties. |
| **Vector Embedding Inversion / Leakage** | Attacker extracts 384-dim embeddings from Neo4j index to reconstruct secret text. | **RAMVectorIndex:** Vector embeddings are computed and held exclusively in process RAM; zero vector embeddings are written to Neo4j properties or disk indexes. Rebuilt dynamically from decrypted facts on startup. |
| **Ciphertext Tampering & Replay Attacks** | Attacker modifies ciphertext bytes or attempts to roll back a fact to an older version. | **Authenticated Associated Data (AAD):** Envelope binds `tenant_id`, `fact_id`, `version`, `scope`, and `valid_from`. Any modification raises `IntegrityMismatchError`. |
| **Search Pattern Leakage** | Attacker monitors query strings in database logs. | **HMAC-SHA256 Blind Indexing:** Predicates, subject keys, and token keywords are transformed into irreversible HMAC blind indexes using per-tenant blind index keys before Cypher queries execute. |
| **Plaintext Leakage Regression** | Developer accidentally writes plaintext property in a new query. | **Zero-Crypto Plaintext Audit Scanner:** Background worker `PlaintextAuditScanner` continuously inspects Neo4j against a strict property allowlist (`id`, `status`, `version`, `blind_index_*`, `ciphertext_*`) and alerts on violations. |

---

## 4. Explicitly Out of Scope
* **Compromised Admin Host / Memory Dump:** Full host compromise with root/process memory access exposes active RAM keys; mitigate via external KMS (AWS KMS, GCP Cloud KMS, HashiCorp Vault) and hardware security modules (HSM).
