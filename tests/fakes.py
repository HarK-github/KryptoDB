"""In-memory test doubles satisfying KryptoDB core protocols."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.turn_analysis import (
    AnalyzedSegment,
    EntityResolution,
    MentionedEntity,
    SegmentType,
    TurnAnalysisResult,
)
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


class FakeTurnAnalyzer:
    """Deterministic test double providing predictable turn analysis for testing."""

    def __init__(self, fixture_overrides: Optional[Dict[str, TurnAnalysisResult]] = None):
        self.fixture_overrides = fixture_overrides or {}

    def analyze_turn(
        self,
        latest_message: str,
        conversation_history: Optional[List[Any]] = None,
        focus_entity: Optional[str] = None,
    ) -> TurnAnalysisResult:
        clean = latest_message.strip()

        # Check explicit fixture overrides
        if clean in self.fixture_overrides:
            return self.fixture_overrides[clean]

        lowered = clean.lower()

        # 1. Chitchat
        if lowered in ("hi", "hello", "hey", "howdy", "greetings", "thanks", "thank you", "bye", "good morning", "good evening", "good afternoon") or lowered.startswith("good morning"):
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.CHITCHAT,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        question_starters = ("what", "where", "who", "when", "why", "how", "tell me", "explain", "does", "do", "can", "compare", "generate", "summarize")
        command_starters = ("confirm", "please confirm", "forget", "clear", "stop", "delete", "make sure", "keep", "keep everything", "constraint")
        raw_sentences = [s.strip() for s in re.split(r'(?<=[.?!])\s+', clean) if s.strip()]
        if len(raw_sentences) > 1:
            segs = []
            ret_query = None
            for sent in raw_sentences:
                s_low = sent.lower()
                is_q = sent.endswith("?") or any(s_low.startswith(q) for q in question_starters) or "summary" in s_low or "sign-off" in s_low or "question" in s_low or "compare" in s_low
                is_retract = "forget" in s_low or "delete" in s_low or "retract" in s_low
                is_cmd = any(s_low.startswith(c) for c in command_starters)
                is_temporal = any(w in s_low for w in ("push the", "delay", "reschedule", "postpone", "cutover back", "by 10 days", "back by"))
                is_correction = any(w in s_low for w in ("had to migrate", "migrate the", "instead", "actually", "update on"))

                if is_q:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.QUESTION, durable=False))
                    if not ret_query:
                        ret_query = sent.rstrip("?").strip()
                elif is_retract:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.RETRACTION, durable=False))
                elif is_cmd:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.COMMAND, durable=False))
                elif is_temporal:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.TEMPORAL_MODIFICATION, durable=True))
                elif is_correction:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.CORRECTION, durable=True))
                else:
                    segs.append(AnalyzedSegment(text=sent, type=SegmentType.STATEMENT, durable=True))

            has_q = any(s.type == SegmentType.QUESTION for s in segs)
            return TurnAnalysisResult(
                segments=segs,
                mentioned_entities=[],
                retrieval_needed=has_q,
                retrieval_query=ret_query if has_q else None,
            )

        # Single command / retraction check
        if any(lowered.startswith(w) for w in ("forget", "delete", "retract", "remove", "clear")) or "forget that" in lowered or "forget my" in lowered:
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.RETRACTION,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        if any(lowered.startswith(w) for w in ("confirm", "please confirm", "stop")):
            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.COMMAND,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=[],
                retrieval_needed=False,
                retrieval_query=None,
            )

        # Pronoun resolution check
        antecedent = focus_entity

        # Check for hypothetical
        is_hypothetical = any(w in lowered for w in ("might try", "maybe i", "could try", "thinking about trying"))

        # Pure Question check
        question_starters = ("what", "where", "who", "when", "why", "how", "tell me", "explain", "does", "do", "can", "compare", "generate", "summarize")
        is_question = clean.endswith("?") or any(lowered.startswith(q) for q in question_starters)

        if is_question:
            resolved_query = clean
            entities = []
            if " he " in f" {lowered} " or lowered.startswith("he "):
                if antecedent:
                    resolved_query = re.sub(r"\bhe\b", antecedent, clean, flags=re.IGNORECASE)
                    entities.append(
                        MentionedEntity(surface="he", resolved_to=antecedent, kind="PERSON", resolution=EntityResolution.COREFERENCE)
                    )
                else:
                    entities.append(
                        MentionedEntity(surface="he", resolved_to=None, kind="PERSON", resolution=EntityResolution.UNRESOLVED)
                    )

            return TurnAnalysisResult(
                segments=[
                    AnalyzedSegment(
                        text=clean,
                        type=SegmentType.QUESTION,
                        durable=False,
                        temporary=False,
                    )
                ],
                mentioned_entities=entities,
                retrieval_needed=True,
                retrieval_query=resolved_query.rstrip("?").strip(),
            )

        # Pure Statement / Preference
        is_pref = any(w in lowered for w in ("likes", "loves", "prefers", "dislikes", "favorite"))
        seg_type = SegmentType.HYPOTHETICAL if is_hypothetical else (SegmentType.PREFERENCE if is_pref else SegmentType.STATEMENT)
        durable = not is_hypothetical

        entities = []
        if re.search(r"\bhe\b", lowered):
            if antecedent:
                entities.append(MentionedEntity(surface="he", resolved_to=antecedent, kind="PERSON", resolution=EntityResolution.COREFERENCE))
            else:
                entities.append(MentionedEntity(surface="he", resolved_to=None, kind="PERSON", resolution=EntityResolution.UNRESOLVED))
                durable = False

        retrieval_needed = lowered.startswith("store fact:")
        retrieval_query = clean if retrieval_needed else None

        return TurnAnalysisResult(
            segments=[
                AnalyzedSegment(
                    text=clean,
                    type=seg_type,
                    durable=durable,
                    temporary=False,
                )
            ],
            mentioned_entities=entities,
            retrieval_needed=retrieval_needed,
            retrieval_query=retrieval_query,
        )

