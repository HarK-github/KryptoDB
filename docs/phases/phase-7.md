# Phase 7: Memory Lifecycle & Conflict Engine

## 1. Summary of Changes
- Implemented `kryptodb/core/memory/predicates.py`:
  - `PredicateRegistry`: Comprehensive cardinality mapping (`ONE` for functional relations vs `MANY` for relational/coexisting relations).
  - Canonical predicate aliases (e.g. `WORKS_AT` -> `WORKS_FOR`, `LIVES_AT` -> `LIVES_IN`).
  - Inverses (e.g. `MANAGES` <-> `MANAGED_BY`, `PARENT_OF` <-> `CHILD_OF`, `OWNS` <-> `OWNED_BY`).
- Implemented `kryptodb/core/memory/lifecycle.py`:
  - Strict lifecycle state machine: `PROPOSED -> ACTIVE`, `ACTIVE -> SUPERSEDED`, `ACTIVE -> REVOKED`.
  - Disallowed transitions (e.g. `REVOKED -> ACTIVE`, `SUPERSEDED -> ACTIVE`) raise `InvalidStateTransitionError`.
- Implemented `kryptodb/core/memory/write_policy.py`:
  - Payload validation on `propose_memory`.
  - Quote substring enforcement: when a quotation is supplied, it must exist as an exact substring of the source conversation event, preventing hallucinated attributions.
- Implemented `kryptodb/core/memory/entity_resolution.py`:
  - Cautious identity resolution: auto-merges ONLY on exact normalized name match.
  - High similarity ($\ge 0.92$) fuzzy matches generate `POSSIBLE_SAME_AS` candidate records for human/LLM review, preventing accidental fusion of distinct individuals.
- Implemented `kryptodb/core/memory/conflicts.py`:
  - Cardinality-driven contradiction handling: `ONE` supersedes prior value with incremented version; `MANY` coexists; exact duplicate refreshes recency without duplicating edges.
- Implemented user memory controls in `kryptodb/core/memory/memory_service.py`:
  - `propose_memory()`, `get_memory()`, `list_memories()`, `correct_memory()`, `forget_memory()`, and `forget_user()`.
- Validated via unit tests in `tests/unit/memory/test_lifecycle.py` and `tests/unit/memory/test_conflicts.py`.

## 2. Verification
```bash
# Architecture linter
uv run lint-imports

# Unit tests
uv run pytest tests/unit/memory/test_lifecycle.py tests/unit/memory/test_conflicts.py
```
