# KryptoDB Old -> New Module Migration Map

This document maps every existing file in `kryptodb/` to its new destination in the modular GraphRAG architecture.

## Target Architecture Layout

```
kryptodb/
  interfaces/
    mcp/            # server.py, tools/ (thin adapters only)
    http/           # routes/, middleware/, schemas/ (thin adapters only)
  core/
    context.py      # RequestContext, tenant/agent/scope claims
    filters.py      # FilterBuilder: THE choke point for all query filters
    memory/         # models.py, memory_service.py, lifecycle.py, conflicts.py,
                    # predicates.py (registry), entity_resolution.py, write_policy.py
    episodic/       # events.py, summaries.py
    retrieval/      # service.py, hybrid_search.py, rrf.py, traversal.py,
                    # ranking.py, memory_context.py, provenance.py
    embeddings/     # embedder.py (port + impl), versioning.py, queue.py
    ports.py        # Protocols: MemoryStore, EventStore, Embedder, Reranker
  infrastructure/
    neo4j/          # client.py, schema.py (indexes), queries.py, store.py
    redis/          # queue/cache only if already used
  workers/          # embedding_retry.py, plaintext_audit.py
  agent_runtime/
    langgraph/      # moved LangGraph code, optional
  daemon/           # app.py (FastAPI factory)
```

---

## File-by-File Mapping

| Original File Path | New Target Location | Status & Migration Strategy |
| :--- | :--- | :--- |
| `kryptodb/daemon/auth.py` | `kryptodb/core/identity/authentication.py` & `kryptodb/core/context.py` | Re-export shim in `daemon/auth.py`. Core logic centralized in `core/identity/`. |
| `kryptodb/daemon/sessions.py` | `kryptodb/core/identity/session_context.py` | Re-export shim in `daemon/sessions.py`. Session tracking moved to `core/identity/`. |
| `kryptodb/daemon/mcp_server.py` | `kryptodb/interfaces/mcp/server.py` | **Removed.** Unified FastMCP server enforces `Authenticator` + `PolicyEngine`. |
| `kryptodb/daemon/server.py` | `kryptodb/daemon/app.py` & `kryptodb/interfaces/http/` | **Removed.** Modularized into `daemon/app.py` and `interfaces/http/routes/`. |
| `kryptodb/daemon/app.py` | `kryptodb/daemon/app.py` | Application factory registering middleware, health, memory, admin, and graph routes. |
| `kryptodb/integrity/partition_crypto.py` | `kryptodb/infrastructure/crypto/` | **Removed** legacy v0.1.0 crypto; replaced by v3 envelope encryption (`KRYPTODB_STORAGE_MODE=encrypted`). |
| `kryptodb/graph/read_engine.py` | `kryptodb/core/retrieval/` & `kryptodb/infrastructure/neo4j/` | **Removed.** Plaintext retrieval with hybrid RRF and 2-hop Cypher traversal. |
| `kryptodb/graph/write_engine.py` | `kryptodb/infrastructure/neo4j/store.py` & `queries.py` | **Removed.** Plaintext write engine unified in `Neo4jMemoryStore` via centralized queries. |
| `kryptodb/memory_api/service.py` | `kryptodb/core/memory/memory_service.py` | **Removed.** Unified `MemoryService` with contradiction detection and state machine. |
| `kryptodb/agent/graph.py` | `kryptodb/agent_runtime/langgraph/graph.py` | **Removed.** LangGraph adapter lives directly in `agent_runtime/langgraph/`. |
| `kryptodb/storage/neo4j_client.py` | `kryptodb/infrastructure/neo4j/client.py` | Underlying Neo4j driver with retry logic. Re-exported in `storage/neo4j_client.py`. |
| `kryptodb/storage/redis_client.py` | `kryptodb/infrastructure/redis/client.py` | Redis connection handling. |
| `kryptodb/core/retrieval/filter_builder.py` | `kryptodb/core/filters.py` | **Choke Point:** Builds Cypher filter fragment enforcing tenant, active scopes, status, and temporal validity. |
| `kryptodb/core/retrieval/hybrid_search.py` | `kryptodb/core/retrieval/hybrid_search.py` & `rrf.py` | Hybrid RRF fusion of vector similarity and BM25 fulltext search with fallback. |
| `kryptodb/core/retrieval/multihop.py` | `kryptodb/core/retrieval/traversal.py` | 2-hop bounded Cypher traversal with cycle suppression ($A \to B \to A$ prevention). |
| `kryptodb/core/retrieval/ranking.py` | `kryptodb/core/retrieval/ranking.py` | Scale-harmonized composite reranking and provenance chain attribution. |
| `kryptodb/core/memory/predicates.py` | `kryptodb/core/memory/predicates.py` | Declarative PredicateRegistry enforcing cardinality (`ONE` vs `MANY`). |
| `kryptodb/core/memory/conflict_engine.py` | `kryptodb/core/memory/conflicts.py` | Consolidated contradiction detection engine superseding older facts on higher confidence. |
| `kryptodb/core/memory/models.py` | `kryptodb/core/memory/models.py` | Pydantic v2 `MemoryRecord` and `MemoryStatus` state machine. |
| `kryptodb/infrastructure/embeddings.py` | `kryptodb/core/embeddings/embedder.py` | SentenceTransformers with fallback and version tracking. |
| `kryptodb/infrastructure/neo4j/schema.py` | `kryptodb/infrastructure/neo4j/schema.py` | Vector and fulltext index creation script. |
