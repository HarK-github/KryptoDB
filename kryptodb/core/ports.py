"""Core protocols and interfaces decoupling domain logic from storage/models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from kryptodb.core.context import RequestContext

if TYPE_CHECKING:
    from kryptodb.core.memory.models import MemoryRecord, MemoryStatus

FactRecord = Any


@dataclass
class ScoredItem:
    item_id: str
    score: float
    item_type: str  # "entity" | "fact" | "episode"
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityRecord:
    id: str
    name: str
    tenant_id: str
    created_at: datetime
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PathStep:
    hop: int
    source: str
    predicate: str
    target: str
    edge_id: str
    confidence: float = 1.0


@dataclass
class PathResult:
    seed: str
    target: str
    hops: int
    score: float
    steps: List[PathStep]
    fact_ids: List[str] = field(default_factory=list)


@dataclass
class Event:
    id: str
    session_id: str
    event_type: str  # "user_message" | "agent_message" | "action"
    content: str
    tenant_id: str = "default"
    scope: str = "default"
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeSummary:
    id: str
    session_id: str
    summary: str
    topics: List[str] = field(default_factory=list)
    tenant_id: str = "default"
    scope: str = "default"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CandidateItem:
    item_id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol for durable graph memory operations."""

    def write_fact(self, fact: MemoryRecord, context: RequestContext) -> MemoryRecord:
        ...

    def get_fact(self, id: str, context: RequestContext) -> Optional[MemoryRecord]:
        ...

    def find_active_by_subject_predicate(
        self, subject: str, predicate: str, context: RequestContext
    ) -> List[MemoryRecord]:
        ...

    def supersede(self, old_id: str, new_id: str, context: RequestContext) -> None:
        ...

    def atomic_supersede(
        self, old_id: str, new_fact: MemoryRecord, context: RequestContext
    ) -> MemoryRecord:
        ...

    def revoke(self, id: str, context: RequestContext) -> None:
        ...

    def search_vector(
        self, query_embedding: List[float], top_k: int, context: RequestContext
    ) -> List[ScoredItem]:
        ...

    def search_fulltext(
        self, query_text: str, top_k: int, context: RequestContext
    ) -> List[ScoredItem]:
        ...

    def list_incident_facts(
        self, subject: str, context: RequestContext, limit: int = 25
    ) -> List[Dict[str, Any]]:
        ...

    def save_entity_profile(
        self,
        entity_id: str,
        scope: str,
        profile_payload: Dict[str, Any],
        context: RequestContext,
    ) -> None:
        ...

    def find_entity(self, name: str, context: RequestContext) -> Optional[EntityRecord]:
        ...

    def traverse_2hop(
        self, seed_names: List[str], context: RequestContext, max_results: int = 25
    ) -> List[PathResult]:
        ...

    def search_entities(
        self, query: str, context: RequestContext, limit: int = 20
    ) -> List[EntityRecord]:
        ...

    def add_entity(
        self, id: str, name: str, context: RequestContext, metadata: Optional[Dict[str, Any]] = None
    ) -> EntityRecord:
        ...

    def revoke_by_user(self, user_id: str, context: RequestContext) -> int:
        ...

    def propose_atomic(
        self,
        subject_norm: str,
        predicate: str,
        object_norm: str,
        new_fact: "MemoryRecord",
        context: RequestContext,
    ) -> "MemoryRecord":
        """Single-transaction atomic first-write guard for Cardinality.ONE facts."""
        ...


@runtime_checkable
class Retriever(Protocol):
    """Protocol for the full GraphRAG retrieval pipeline (plaintext or encrypted).

    Both PlaintextNeo4jRetriever and EncryptedRetrievalService must implement this exact signature.
    """

    def search(
        self,
        query: str,
        context: RequestContext,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
    ) -> List[Any]: ...  # List[AttributedMemoryResult] — imported from retrieval.ranking at runtime


@runtime_checkable
class EventStore(Protocol):
    """Protocol for episodic conversation events and summaries."""

    def append(self, event: Event, context: RequestContext) -> str:
        ...

    def list_by_session(self, session_id: str, context: RequestContext) -> List[Event]:
        ...

    def store_summary(self, summary: EpisodeSummary, context: RequestContext) -> str:
        ...


@runtime_checkable
class Embedder(Protocol):
    """Protocol for dense embedding generation."""

    @property
    def dimension(self) -> int:
        ...

    @property
    def model_name(self) -> str:
        ...

    @property
    def model_version(self) -> str:
        ...

    def embed(self, text: str) -> List[float]:
        ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """Protocol for reranking candidate memories."""

    def rerank(
        self, query: str, items: List[CandidateItem]
    ) -> List[Tuple[CandidateItem, float]]:
        ...
