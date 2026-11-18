"""Provenance formatting and citation generation."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence
from pydantic import BaseModel


class ProvenanceStep(BaseModel):
    hop: int
    source: str
    predicate: str
    target: str
    edge_id: str
    description: Optional[str] = None
    scope: str = "default"
    confidence: float = 1.0


def format_citation(
    tenant_id: str,
    scopes: Sequence[str],
    edge_ids: Sequence[str]
) -> str:
    """Formats standardized citation tag e.g. [tenant:acme, scope:default, edges:e1->e2]."""
    scope_str = ",".join(sorted(set(scopes))) if scopes else "default"
    edges_str = "->".join(edge_ids) if edge_ids else "none"
    return f"[tenant:{tenant_id}, scope:{scope_str}, edges:{edges_str}]"


def format_provenance_chain(steps: Sequence[ProvenanceStep]) -> str:
    """Formats human-readable provenance graph path: (A)-[PRED]->(B)-[PRED]->(C)."""
    if not steps:
        return ""
    chain_parts = []
    for idx, s in enumerate(steps):
        if idx == 0:
            chain_parts.append(f"({s.source})-[{s.predicate}]->({s.target})")
        else:
            chain_parts.append(f"-[{s.predicate}]->({s.target})")
    return "".join(chain_parts)
