"""Thread-safe daemon-side in-RAM vector index (v3 Phase 2 spec).

Vectors live strictly in daemon memory per scope and are never written to Neo4j.
Updated incrementally on committed writes, supersessions, and forgets, and
rebuilt at startup by streaming and decrypting active facts.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class RAMVectorIndex:
    """Per-scope in-RAM cosine similarity index mapping fact_id -> unit vector."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scopes: Dict[str, Dict[str, List[float]]] = {}

    def upsert(self, scope: str, fact_id: str, vector: List[float]) -> None:
        if not vector:
            return
        unit = _l2_normalize(vector)
        with self._lock:
            self._scopes.setdefault(scope, {})[fact_id] = unit

    def remove(self, scope: str, fact_id: str) -> bool:
        with self._lock:
            bucket = self._scopes.get(scope)
            if bucket and fact_id in bucket:
                del bucket[fact_id]
                return True
            return False

    def clear_scope(self, scope: str) -> None:
        with self._lock:
            self._scopes.pop(scope, None)

    def count(self, scope: Optional[str] = None) -> int:
        with self._lock:
            if scope is not None:
                return len(self._scopes.get(scope, {}))
            return sum(len(b) for b in self._scopes.values())

    def search(self, scope: str, query_vector: List[float], top_k: int = 10) -> List[Tuple[str, float]]:
        if not query_vector or top_k <= 0:
            return []
        q_unit = _l2_normalize(query_vector)
        with self._lock:
            bucket = dict(self._scopes.get(scope, {}))
        if not bucket:
            return []

        scored: List[Tuple[str, float]] = []
        for fact_id, vec in bucket.items():
            if len(vec) == len(q_unit):
                scored.append((fact_id, float(_dot(q_unit, vec))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def rebuild_from_facts(
        self,
        scope: str,
        facts: List[Any],
        embed_fn: Callable[[str], List[float]],
    ) -> Dict[str, Any]:
        """Rebuilds a scope's RAM vector index from decrypted active MemoryRecords."""
        start = time.perf_counter()
        new_bucket: Dict[str, List[float]] = {}
        for fact in facts:
            text = (
                getattr(fact, "description", None)
                or f"{getattr(fact, 'subject', '')} {getattr(fact, 'predicate', '')} {getattr(fact, 'object', '')}"
            ).strip()
            if not text:
                continue
            vec = embed_fn(text)
            if vec:
                new_bucket[str(fact.id)] = _l2_normalize(vec)
        with self._lock:
            self._scopes[scope] = new_bucket
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return {"scope": scope, "indexed_facts": len(new_bucket), "rebuild_ms": round(elapsed_ms, 2)}
