# Phase 4: Schema, Embeddings, Episodic Layer

## 1. Summary of Changes
- Implemented `kryptodb/infrastructure/neo4j/schema.py`:
  - Constraints: `Entity(tenant_id, name)` unique; `Event(id)` unique; `Episode(id)` unique; `FACT(id)` unique.
  - Fulltext indexes: `entity_name_ft` on `Entity.name`; `fact_text_ft` on `FACT.predicate, FACT.description`.
  - Vector indexes: `entity_embedding_idx` (384-d cosine), `fact_embedding_idx` (384-d cosine on relationship), and `episode_embedding_idx` (384-d cosine).
  - Executed and validated `ensure_schema()` against live Neo4j 5.21.2 Community instance.
- Implemented `kryptodb/core/embeddings/`:
  - `embedder.py`: `LocalSentenceEmbedder` implementing `Embedder` protocol.
  - `versioning.py`: `EmbeddingMetadata` tracking dimension, model, and version with compatibility validation.
  - `queue.py`: `embed_with_resilience` returning `(None, True)` on timeout/failure without blocking writes.
- Implemented `kryptodb/workers/embedding_retry.py`: Background worker querying `embedding_pending = true` on edges and entities to compute and persist vectors asynchronously.
- Implemented `kryptodb/core/episodic/`:
  - `events.py`: `record_event()` for durable episodic conversation turn logging.
  - `summaries.py`: `summarize_session()` for session aggregation, key entity extraction, and `:Episode` node persistence.
- Added comprehensive unit tests in `tests/unit/memory/test_episodic_and_embeddings.py`.

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Unit tests
uv run pytest tests/unit/memory/test_episodic_and_embeddings.py
```
