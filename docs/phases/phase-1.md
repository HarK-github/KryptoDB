# Phase 1: Move and Shim

## 1. Summary of Changes
- Established target directory structure:
  - `kryptodb/interfaces/mcp/tools/`
  - `kryptodb/interfaces/http/middleware/`, `kryptodb/interfaces/http/schemas/`
  - `kryptodb/core/episodic/`
  - `kryptodb/core/embeddings/`
  - `kryptodb/core/observability.py`
  - `kryptodb/infrastructure/neo4j/`
  - `kryptodb/infrastructure/redis/`
  - `kryptodb/agent_runtime/langgraph/`
  - `kryptodb/legacy/crypto/`
  - `kryptodb/workers/`
- Relocated LangGraph implementation to `kryptodb/agent_runtime/langgraph/graph.py` with backward-compatible re-export shim raising `DeprecationWarning` at `kryptodb/agent/graph.py`.
- Relocated partition crypto implementation to `kryptodb/legacy/crypto/partition_crypto.py` with backward-compatible re-export shim raising `DeprecationWarning` at `kryptodb/integrity/partition_crypto.py`.
- Relocated client storage drivers to `kryptodb/infrastructure/neo4j/client.py` and `kryptodb/infrastructure/redis/client.py` with re-export shims at `kryptodb/storage/neo4j_client.py` and `kryptodb/storage/redis_client.py`.
- Configured OpenTelemetry tracer in `kryptodb/core/observability.py` (`tracer = trace.get_tracer("kryptodb")`).
- Verified zero broken dependencies with `uv run lint-imports` (2 kept, 0 broken, 51 files analyzed).
- Maintained 100% unit test pass rate (90 tests passing).

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Unit tests
KRYPTODB_MASTER_KEY=000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f uv run pytest tests/unit/
```

## 3. Known Gaps
- `core/context.py` and `core/filters.py` will be created in Phase 2 as the formal choke point for all tenant, scope, status, and time-validity filters.
