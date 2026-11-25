# ADR 0002: Predicate Cardinality for `WORKS_FOR`

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

`PredicateRegistry` classifies predicates as either `Cardinality.ONE` (functional; a new value supersedes the prior active edge) or `Cardinality.MANY` (relational; multiple active values coexist). `WORKS_FOR` was previously configured as `Cardinality.MANY`, which meant statements like *"Alice now works for Acme"* left *"Alice works for OldCorp"* active simultaneously, breaking temporal supersession queries for primary employment.

## Decision

Set `WORKS_FOR` to `Cardinality.ONE` in `kryptodb/core/memory/predicates.py`.

- A person's primary employer is treated as functional (`WORKS_FOR` supersedes the previous active `WORKS_FOR` edge while preserving historical validity windows via `valid_until` / `status = 'superseded'`).
- Multi-organization affiliations (advisory roles, committees, open-source projects) should use `MEMBER_OF` or `CONTRIBUTES_TO`, which remain `Cardinality.MANY`.

## Consequences

- Career changes automatically supersede prior `WORKS_FOR` facts without leaving stale employers active.
- Historical queries ("Where did Alice previously work?") continue to resolve via superseded temporal edges.
