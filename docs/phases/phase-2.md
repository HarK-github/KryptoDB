# Phase 2: RequestContext and Filter Choke Point

## 1. Summary of Changes
- Implemented `kryptodb/core/context.py`:
  - `RequestContext`: Frozen dataclass capturing `tenant_id`, `agent_id`, `user_id`, `session_id`, `active_scopes` (frozen set), `now` (timezone-aware UTC datetime), and `is_system`.
  - Factory method `RequestContext.create()` with strict validation for non-empty identifier strings and timezone enforcement.
  - `create_system_context()` and `SYSTEM_CONTEXT` constant for background workers, data migrations, and internal tasks.
- Implemented `kryptodb/core/filters.py`:
  - `FilterBuilder.build()`: The single choke point for generating parameterized Cypher WHERE clauses on relationship edges (`alias.tenant_id = $tenant_id AND alias.scope IN $active_scopes AND alias.status = 'active' AND alias.valid_from <= $now AND (alias.valid_until IS NULL OR alias.valid_until > $now)`).
  - Empty scope safety: If `active_scopes` is empty and not in system mode, outputs `1 = 0` preventing unauthorized leaks.
  - System mode handling: Omit scope filtering while enforcing tenant, status, and validity.
  - Replaced `kryptodb/core/retrieval/filter_builder.py` with a deprecation shim pointing to `kryptodb.core.filters`.
- Added `tests/unit/retrieval/test_filter_choke_point.py`:
  - Validates context construction and bounds checking.
  - Validates all branches of `FilterBuilder` (normal scopes, empty scopes -> `1 = 0`, system mode, alias customization).
  - AST / regex audit scanning `core/` and `infrastructure/` to verify every `:FACT` Cypher query is protected by filter variables or FilterBuilder.
- Maintained 100% test pass rate and 0 broken import contracts.

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Unit tests
uv run pytest tests/unit/retrieval/test_filter_choke_point.py
```
