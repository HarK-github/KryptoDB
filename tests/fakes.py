"""In-memory test doubles satisfying KryptoDB core protocols."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.core.ports import (
    CandidateItem,
    Embedder,
    EntityRecord,
    EpisodeSummary,
    Event,
    EventStore,
    MemoryStore,
    PathResult,
    PathStep,
    Reranker,
    ScoredItem,
)


class FakeEmbedder:
    """Deterministic hash-based embedder generating fixed-dimension unit vectors."""

    def __init__(self, dimension: int = 384):
        self._dim = dimension
        self._model_name = "fake-minilm"
        self._model_version = "1.0.0"

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        return self._model_version

    def embed(self, text: str) -> List[float]:
        # Hash text to pseudo-random float vector
        cleaned = text.strip().lower()
        vec = []
        for i in range(self._dim):
            h = hashlib.sha256(f"{cleaned}:{i}".encode("utf-8")).digest()
            val = (int.from_bytes(h[:4], "big") / (2**32 - 1)) * 2.0 - 1.0
            vec.append(val)
        # Normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class FakeMemoryStore:
    """In-memory implementation of MemoryStore for unit tests without external databases."""

    def __init__(self, embedder: Optional[Embedder] = None):
        self.facts: Dict[str, MemoryRecord] = {}
        self.entities: Dict[Tuple[str, str], EntityRecord] = {}
        self.embedder = embedder or FakeEmbedder()

    def _matches_context(self, fact: MemoryRecord, context: RequestContext) -> bool:
        if fact.tenant_id != context.tenant_id:
            return False
        if not context.is_system:
            if not context.active_scopes:
                return False
            if fact.scope not in context.active_scopes:
                return False
        if fact.status != MemoryStatus.ACTIVE:
            return False
        # Allow 5-second tolerance for sub-second clock skew between context creation and record insertion
        from datetime import timedelta
        if fact.valid_from and fact.valid_from > context.now + timedelta(seconds=5):
            return False
        if fact.valid_until and fact.valid_until <= context.now:
            return False
        return True

    def add_entity(
        self, id: str, name: str, context: RequestContext, metadata: Optional[Dict[str, Any]] = None
    ) -> EntityRecord:
        record = EntityRecord(
            id=id,
            name=name,
            tenant_id=context.tenant_id,
            created_at=context.now,
            embedding=self.embedder.embed(name),
            metadata=metadata or {},
        )
        self.entities[(context.tenant_id, name.lower())] = record
        return record

    def write_fact(self, fact: MemoryRecord, context: RequestContext) -> MemoryRecord:
        # Create entities if they don't exist
        for name in (fact.subject, fact.object):
            key = (fact.tenant_id, name.lower())
            if key not in self.entities:
                self.entities[key] = EntityRecord(
                    id=f"ent-{len(self.entities)+1}",
                    name=name,
                    tenant_id=fact.tenant_id,
                    created_at=context.now,
                    embedding=self.embedder.embed(name),
                )
        self.facts[fact.id] = fact
        return fact

    def get_fact(self, id: str, context: RequestContext) -> Optional[MemoryRecord]:
        fact = self.facts.get(id)
        if fact and self._matches_context(fact, context):
            return fact
        return None

    def find_active_by_subject_predicate(
        self, subject: str, predicate: str, context: RequestContext
    ) -> List[MemoryRecord]:
        matches = []
        for fact in self.facts.values():
            if (
                fact.subject.lower() == subject.lower()
                and fact.predicate.upper() == predicate.upper()
                and self._matches_context(fact, context)
            ):
                matches.append(fact)
        return matches

    def supersede(self, old_id: str, new_id: str, context: RequestContext) -> None:
        if old_id in self.facts:
            old = self.facts[old_id]
            updated = old.model_copy(
                update={
                    "status": MemoryStatus.SUPERSEDED,
                    "valid_until": context.now,
                }
            )
            self.facts[old_id] = updated

    def atomic_supersede(self, old_id: str, new_fact: MemoryRecord, context: RequestContext) -> MemoryRecord:
        self.supersede(old_id, new_fact.id, context)
        return self.write_fact(new_fact, context)

    def revoke(self, id: str, context: RequestContext) -> None:
        if id in self.facts:
            old = self.facts[id]
            updated = old.model_copy(
                update={
                    "status": MemoryStatus.REVOKED,
                    "valid_until": context.now,
                }
            )
            self.facts[id] = updated

    def search_vector(
        self, query_embedding: List[float], top_k: int, context: RequestContext
    ) -> List[ScoredItem]:
        scored: List[ScoredItem] = []
        for fact in self.facts.values():
            if not self._matches_context(fact, context):
                continue
            desc = fact.description or f"{fact.subject} {fact.predicate} {fact.object}"
            emb = self.embedder.embed(desc)
            dot = sum(a * b for a, b in zip(query_embedding, emb))
            scored.append(
                ScoredItem(
                    item_id=fact.id,
                    score=float(dot),
                    item_type="fact",
                    text=desc,
                    metadata={"subject": fact.subject, "predicate": fact.predicate, "object": fact.object},
                )
            )
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    def search_fulltext(
        self, query_text: str, top_k: int, context: RequestContext
    ) -> List[ScoredItem]:
        scored: List[ScoredItem] = []
        query_words = set(query_text.lower().split())
        for fact in self.facts.values():
            if not self._matches_context(fact, context):
                continue
            desc = (fact.description or f"{fact.subject} {fact.predicate} {fact.object}").lower()
            overlap = sum(1 for w in query_words if w in desc)
            if overlap > 0:
                score = overlap / len(query_words)
                scored.append(
                    ScoredItem(
                        item_id=fact.id,
                        score=score,
                        item_type="fact",
                        text=fact.description or f"{fact.subject} {fact.predicate} {fact.object}",
                        metadata={"subject": fact.subject, "predicate": fact.predicate, "object": fact.object},
                    )
                )
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]

    def find_entity(self, name: str, context: RequestContext) -> Optional[EntityRecord]:
        key = (context.tenant_id, name.lower())
        return self.entities.get(key)

    def traverse_2hop(
        self, seed_names: List[str], context: RequestContext, max_results: int = 25
    ) -> List[PathResult]:
        paths: List[PathResult] = []
        seed_set = {s.lower() for s in seed_names}

        # 1-hop paths
        for f1 in self.facts.values():
            if not self._matches_context(f1, context):
                continue
            if f1.subject.lower() in seed_set:
                step1 = PathStep(
                    hop=1,
                    source=f1.subject,
                    predicate=f1.predicate,
                    target=f1.object,
                    edge_id=f1.id,
                    confidence=f1.confidence,
                )
                paths.append(
                    PathResult(
                        seed=f1.subject,
                        target=f1.object,
                        hops=1,
                        score=f1.confidence,
                        steps=[step1],
                        fact_ids=[f1.id],
                    )
                )

                # 2-hop traversal: avoid cycle (f2.object != f1.subject)
                for f2 in self.facts.values():
                    if not self._matches_context(f2, context):
                        continue
                    if f2.id == f1.id:
                        continue
                    if f2.subject.lower() == f1.object.lower() and f2.object.lower() != f1.subject.lower():
                        step2 = PathStep(
                            hop=2,
                            source=f2.subject,
                            predicate=f2.predicate,
                            target=f2.object,
                            edge_id=f2.id,
                            confidence=f2.confidence,
                        )
                        combined_conf = f1.confidence * f2.confidence
                        paths.append(
                            PathResult(
                                seed=f1.subject,
                                target=f2.object,
                                hops=2,
                                score=combined_conf,
                                steps=[step1, step2],
                                fact_ids=[f1.id, f2.id],
                            )
                        )

        return paths[:max_results]


class FakeEventStore:
    """In-memory event store for conversation history and summaries."""

    def __init__(self):
        self.events: List[Event] = []
        self.summaries: Dict[str, EpisodeSummary] = {}

    def append(self, event: Event, context: RequestContext) -> str:
        self.events.append(event)
        return event.id

    def list_by_session(self, session_id: str, context: RequestContext) -> List[Event]:
        return [
            e for e in self.events
            if e.session_id == session_id and e.tenant_id == context.tenant_id
        ]

    def store_summary(self, summary: EpisodeSummary, context: RequestContext) -> str:
        self.summaries[summary.id] = summary
        return summary.id


class FakeReranker:
    """Deterministic reranker based on lexical match scoring."""

    def rerank(
        self, query: str, items: List[CandidateItem]
    ) -> List[Tuple[CandidateItem, float]]:
        q_words = set(query.lower().split())
        scored = []
        for it in items:
            it_words = set(it.text.lower().split())
            jaccard = len(q_words & it_words) / max(1, len(q_words | it_words))
            scored.append((it, float(jaccard)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
