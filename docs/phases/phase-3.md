# Phase 3: Ports and Storage Refactor

## 1. Summary of Changes
- Implemented core domain protocols in `kryptodb/core/ports.py` using `typing.Protocol` and `@runtime_checkable`:
  - `MemoryStore`: Graph fact storage, single/multi-hop traversal, vector/fulltext search, supersession, and revocation.
  - `EventStore`: Episodic conversation append, session listing, and episode summaries.
  - `Embedder`: Dense vector generation contract with dimension, model_name, and model_version metadata.
  - `Reranker`: Cross-encoder and ranking contract.
- Created in-memory test doubles in `tests/fakes.py`:
  - `FakeEmbedder`: Deterministic hash-based 384-d normalized vector generation.
  - `FakeMemoryStore`: Dict-backed in-memory store supporting vector similarity, fulltext search, 2-hop traversal, and conflict lifecycle.
  - `FakeEventStore`: In-memory episodic event history and summary persistence.
  - `FakeReranker`: Lexical Jaccard-based scoring test double.
- Implemented `kryptodb/infrastructure/neo4j/queries.py`: Extracted all Cypher queries into external constants with parameterized filter slots.
- Implemented `kryptodb/infrastructure/neo4j/store.py`: `Neo4jMemoryStore` implementing `MemoryStore` and `EventStore` using `FilterBuilder` to parameterize all queries.
- Cleanly decoupled cryptographic code from live paths: verified 0 imports from `legacy/crypto/` across `core/` and `infrastructure/`.
- Verified core domain tests pass against `FakeMemoryStore` without requiring external databases.

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Core domain test with fake store
uv run pytest tests/unit/memory/test_core_with_fake_store.py
```
