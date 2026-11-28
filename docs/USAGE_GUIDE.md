# KryptoDB — How It Works & How to Use It Anywhere

KryptoDB is a **scoped, temporal, cryptographically verifiable knowledge-graph memory engine** for AI agents and applications. It combines Neo4j graph traversal, hybrid vector + BM25 search, strict predicate cardinality/conflict resolution, and optional client-side envelope encryption (`AES-256-GCM` + HMAC blind indexes) behind a clean **Hexagonal (Ports & Adapters)** architecture.

---

## 1. How KryptoDB Works

### 1.1. 4-Layer Hexagonal Architecture

```text
  [ HTTP REST (/api/v1) ]   [ MCP Server ]   [ LangGraph Agent ]   [ Python SDK ]
            │                     │                   │                   │
            └─────────────────────┴───────────────────┴───────────────────┘
                                          │
                                          ▼
                        kryptodb/core/ (Zero Driver/Web Imports)
                ┌───────────────────────────────────────────────────────┐
                │  RequestContext + PolicyEngine + FilterBuilder        │
                │  MemoryService  (propose, correct, forget, summarize) │
                │  RetrievalService (Hybrid RRF + Multi-hop + Rerank)   │
                │  EntityResolver (exact + Block-then-LLM AliasArbiter) │
                │  EntitySummarizer (degree >= 3 Hub EntityProfile)     │
                └─────────────────────────┬─────────────────────────────┘
                                          │  kryptodb/core/ports.py
                                          │  (MemoryStore, Retriever,
                                          │   EventStore, Embedder, Reranker)
                                          ▼
                   kryptodb/infrastructure/ (Pluggable Adapters)
                ┌─────────────────────────┴─────────────────────────────┐
                │  Plaintext Mode:                                      │
                │    Neo4jMemoryStore + PlaintextNeo4jRetriever         │
                │  Encrypted Mode (v3):                                 │
                │    EncryptedNeo4jMemoryStore + EncryptedRetrievalSvc  │
                │    + EnvelopeCipher (AES-256-GCM) + RAMVectorIndex    │
                └───────────────────────────────────────────────────────┘
```

- **Strict Core Isolation:** `kryptodb/core/` has **zero** imports of `neo4j`, `redis`, `fastapi`, `sentence_transformers`, or `kryptodb.infrastructure`. All concrete adapters are wired once in the composition root ([`wire_runtime_services`](file:///mnt/DE94962594960067/Users/kandp/Documents/Work/Git_Files/AIAgent/kryptodb/daemon/app.py)).

### 1.2. Write Path (`propose_memory`)

1. **Authentication & RBAC (`Authenticator` + `PolicyEngine`):**
   Every request creates an immutable `RequestContext` (`tenant_id`, `agent_id`, `active_scopes`, `role`, `permissions`). API keys are SHA-256 hashed in SQLite (`KRYPTODB_KEY_DB_PATH`). The daemon fails closed if `KRYPTODB_ADMIN_API_KEY` is missing or the SQLite file cannot be opened.
2. **Write Policy (`validate_proposal`):**
   Rejects vague predicates (e.g., `RELATED_TO`) and verifies quotation grounding when `quote_substring` / `source_event_id` is supplied.
3. **Entity Resolution (`EntityResolver`):**
   - **Stage 1:** Exact normalized name + compatible entity type auto-merges deterministically.
   - **Stage 2:** Near matches (edit distance, prefix, vector similarity, or shared-token blocking) produce `POSSIBLE_SAME_AS` candidates. When an optional `AliasArbiter` callback is configured, it arbitrates candidates with two safety invariants: omitted candidates fall back to singletons, and incompatible types or `< 0.90` confidence merges are split back to singletons.
4. **Cardinality & Atomic Concurrency (`propose_atomic`):**
   - **`Cardinality.ONE`** (e.g., `LIVES_IN`, `WORKS_FOR`, `LEAD_ARCHITECT`, `USES`): Executed in a **single Neo4j transaction** that acquires a lock on the subject `:Entity` (`SET s._write_lock = true`), checks active edges, and atomically deduplicates, supersedes (when `confidence >= existing.confidence`), or flags a `conflicted` edge.
   - **`Cardinality.MANY`** (e.g., `MEMBER_OF`, `HAS_SKILL`, `OWNS`): Multiple active edges coexist.
5. **Hub Entity Summarizer (`EntitySummarizer`):**
   When an entity's active incident `:FACT` degree reaches `KRYPTODB_HUB_SUMMARY_MIN_DEGREE` (default `3`), `EntitySummarizer` builds an `EntityProfile` (`summary`, `time_range_start`, `time_range_end`, `key_facts`, `source_fact_ids`) and persists it (`profile_json` in plaintext mode; `AES-256-GCM` sealed `profile_ct` with `AAD = kdb:v1:{scope}:{entity_id}:profile` in encrypted mode).

### 1.3. Read Path (`search`)

- **Plaintext Mode (`PlaintextNeo4jRetriever`):**
  1. **Hybrid Seed Discovery:** Neo4j vector index + Lucene BM25 fulltext fused via Reciprocal Rank Fusion (`RRF`, `k=60`), with semantic vector fallback if lexical seeds are empty.
  2. **Cycle-Safe 2-Hop Traversal (`MultiHopTraverser`):** Expands 1-hop and 2-hop paths with `FilterBuilder` scope/tenant/temporal predicates enforced on every edge, plus hub-degree suppression.
  3. **3-Stage Reranking (`PathReranker`):** Combines relevance (Cross-Encoder or lexical overlap), confidence, recency decay, and hop penalty.
- **Encrypted Mode (`EncryptedRetrievalService`):**
  1. **Path A (Semantic):** Searches the daemon's in-memory `RAMVectorIndex` for top-k `fact_id`s, then fetches encrypted neighborhoods from Neo4j.
  2. **Path B (Blind Index):** Computes keyed HMAC-SHA256 tokens (`subject_bidx`, `object_bidx`, `search_tokens`) to match encrypted edges without exposing plaintext to Neo4j.
  3. **Fuse & Decrypt:** Fuses Path A + Path B via RRF, decrypts and verifies AES-256-GCM AAD tags in daemon RAM, respects `max_hops` (`0` skips 2-hop neighbors) and `relevance_cutoff`, and returns `AttributedMemoryResult`s.

---

## 2. How to Use KryptoDB Anywhere

### Prerequisites

Start Neo4j (and Redis if using queues):

```bash
docker compose up -d neo4j redis
```

Install dependencies with `uv`:

```bash
uv sync
```

---

### Option 1 — Run as a Standalone HTTP REST Service (Any Language)

Start the FastAPI daemon:

```bash
export KRYPTODB_ADMIN_API_KEY="your-strong-admin-secret"
export KRYPTODB_STORAGE_MODE="plaintext"   # or "encrypted"
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="password"

uv run uvicorn kryptodb.daemon.app:app --host 0.0.0.0 --port 8000
```

**1. Create an agent API key (Admin):**

```bash
curl -s -X POST http://localhost:8000/admin/api-keys \
  -H "Authorization: Bearer $KRYPTODB_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "my-app-agent",
    "tenant_id": "acme",
    "role": "writer",
    "scopes": ["engineering", "default"]
  }'
```

**2. Propose a fact (`POST /api/v1/memory/propose`):**

```bash
curl -s -X POST http://localhost:8000/api/v1/memory/propose \
  -H "Authorization: Bearer <AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Alice",
    "predicate": "WORKS_FOR",
    "object": "Acme Corp",
    "scope": "engineering",
    "confidence": 0.95
  }'
```

**3. Search memory (`POST /api/v1/memory/search`):**

```bash
curl -s -X POST http://localhost:8000/api/v1/memory/search \
  -H "Authorization: Bearer <AGENT_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Where does Alice work?",
    "scopes": ["engineering"],
    "top_k": 5,
    "max_hops": 2
  }'
```

**4. Correct, forget, or GDPR-erase a user:**
- `PATCH /api/v1/memory/{id}` — supersede a fact with a corrected value
- `DELETE /api/v1/memory/{id}` — revoke a single fact
- `DELETE /api/v1/users/{user_id}/memories` — revoke all facts and purge `:Event` / `:Episode` nodes for `user_id`
- `GET /healthz` — health check (also surfaces non-sensitive `plaintext_audit` counts in encrypted mode)

---

### Option 2 — Use via MCP (Claude Desktop, Cursor, Antigravity)

KryptoDB ships a FastMCP server exposing `propose_memory`, `query_memory`, `list_memories`, `correct_memory`, `forget_memory`, `record_event`, `remember`, `recall`, and `session_activate`.

Add to your MCP client config (e.g., `claude_desktop_config.json` or Cursor MCP settings):

```json
{
  "mcpServers": {
    "kryptodb": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/KryptoDB", "python", "-m", "kryptodb.interfaces.mcp.server"],
      "env": {
        "KRYPTODB_ADMIN_API_KEY": "your-strong-admin-secret",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "password"
      }
    }
  }
}
```

---

### Option 3 — Embed Directly as a Python Library

Because `kryptodb/core/` is framework-free, you can wire `MemoryService` and `RetrievalService` directly inside any Python service, CLI, or worker:

```python
from kryptodb.core.context import RequestContext
from kryptodb.core.memory.entity_resolution import EntityResolver
from kryptodb.core.memory.entity_summarizer import EntitySummarizer
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.core.retrieval.hybrid_search import HybridSearchEngine, configure_hybrid_search
from kryptodb.core.retrieval.ranking import PathReranker
from kryptodb.core.retrieval.service import RetrievalService
from kryptodb.core.retrieval.traversal import MultiHopTraverser, configure_traversal
from kryptodb.infrastructure.embeddings.sentence_transformer import LocalSentenceEmbedder
from kryptodb.infrastructure.neo4j import queries as q
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.plaintext_retriever import PlaintextNeo4jRetriever
from kryptodb.infrastructure.neo4j.store import Neo4jMemoryStore

# 1. Connect Neo4j & inject Cypher queries
neo4j_client.connect()
configure_hybrid_search(neo4j_client, {
    "HYBRID_VECTOR_ENTITY_QUERY": q.HYBRID_VECTOR_ENTITY_QUERY,
    "HYBRID_FULLTEXT_ENTITY_QUERY": q.HYBRID_FULLTEXT_ENTITY_QUERY,
    "SEMANTIC_SEED_FALLBACK_ENTITY_QUERY": q.SEMANTIC_SEED_FALLBACK_ENTITY_QUERY,
    "SEMANTIC_SEED_FALLBACK_FACT_QUERY": q.SEMANTIC_SEED_FALLBACK_FACT_QUERY,
})
configure_traversal(q.TRAVERSAL_1HOP_QUERY, q.TRAVERSAL_2HOP_EXPANSION_QUERY)

# 2. Wire adapters
embedder = LocalSentenceEmbedder(allow_fallback=True)
store = Neo4jMemoryStore(client=neo4j_client)
memory = MemoryService(
    store=store,
    entity_resolver=EntityResolver(),
    entity_summarizer=EntitySummarizer(storage_mode="plaintext"),
)
retriever = PlaintextNeo4jRetriever(
    hybrid_engine=HybridSearchEngine(client=neo4j_client, embedder=embedder),
    traverser=MultiHopTraverser(client=neo4j_client),
    reranker=PathReranker(),
    client=neo4j_client,
    retrospective_query=q.RETROSPECTIVE_FACTS_QUERY,
)
retrieval = RetrievalService(client=neo4j_client, retriever=retriever)

# 3. Use with a RequestContext
ctx = RequestContext.create(tenant_id="acme", agent_id="worker-1", active_scopes=["engineering"])
memory.propose_memory("Service Alpha", "DEPENDS_ON", "Cluster Borealis", context=ctx)
hits = retrieval.search("What does Service Alpha depend on?", tenant_id="acme", active_scopes=["engineering"])
```

---

### Option 4 — Enable Zero-Knowledge Encrypted Storage Mode

To ensure Neo4j never sees plaintext facts, entity names, or hub summaries:

**1. Generate a 256-bit JSON key bundle (`enc_key` + distinct `index_key`):**

```bash
python3 -c '
import base64, json, os
key = {
  "current_kid": "k1",
  "enc_key": base64.b64encode(os.urandom(32)).decode("ascii"),
  "index_key": base64.b64encode(os.urandom(32)).decode("ascii"),
}
with open("/tmp/kdb.key", "w") as f:
    json.dump(key, f, indent=2)
'
```

**2. Boot the daemon in encrypted mode:**

```bash
export KRYPTODB_ADMIN_API_KEY="your-strong-admin-secret"
export KRYPTODB_STORAGE_MODE="encrypted"
export KRYPTODB_KEY_FILE="/tmp/kdb.key"
export KRYPTODB_ENCRYPTED_SCOPES="default,research,engineering"
export KRYPTODB_AUDIT_INTERVAL_SECONDS="300"

uv run uvicorn kryptodb.daemon.app:app --host 0.0.0.0 --port 8000
```

In encrypted mode, `PlaintextAuditScanner` automatically runs on the configured interval to verify zero disallowed plaintext properties exist on Neo4j nodes/edges, and reports scan status on `GET /healthz`.

---

## 3. Key Configuration Reference

| Environment Variable | Default | Purpose |
| :--- | :--- | :--- |
| `KRYPTODB_ADMIN_API_KEY` | *(required)* | Admin Bearer token; daemon refuses to boot if unset. |
| `KRYPTODB_KEY_DB_PATH` | `data/api_keys.db` | SQLite path for SHA-256 hashed API keys (fails closed on error; `:memory:` allowed for tests). |
| `KRYPTODB_STORAGE_MODE` | `plaintext` | `plaintext` or `encrypted` (`AES-256-GCM` + blind indexes). |
| `KRYPTODB_KEY_FILE` | *(required if encrypted)* | Path to JSON keyring (`current_kid`, `enc_key`, `index_key`). |
| `KRYPTODB_ENCRYPTED_SCOPES` | `default,research,engineering` | Scopes whose `RAMVectorIndex` is rebuilt on daemon startup. |
| `KRYPTODB_AUDIT_INTERVAL_SECONDS` | `300` | Interval for background `PlaintextAuditScanner` in encrypted mode. |
| `KRYPTODB_HUB_SUMMARY_MIN_DEGREE` | `3` | Minimum active `:FACT` degree before `EntitySummarizer` writes an `EntityProfile`. |
| `KRYPTODB_USE_REAL_EMBEDDINGS` | `0` | Set `1` to load HuggingFace `SentenceTransformer` + `CrossEncoder` instead of deterministic fallback. |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | `bolt://localhost:7687` | Neo4j connection settings. |
