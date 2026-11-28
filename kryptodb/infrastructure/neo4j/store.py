"""Neo4j implementation of MemoryStore and EventStore protocols."""

from __future__ import annotations

import json
import logging
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kryptodb.core.context import RequestContext
from kryptodb.core.filters import FilterBuilder
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus, MemoryType
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
    ScoredItem,
)
from kryptodb.infrastructure.embeddings import embedding_service
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.queries import (
    APPEND_EVENT_QUERY,
    ATOMIC_SUPERSEDE_QUERY,
    ATOMIC_WRITE_CARDINALITY_ONE_QUERY,
    ADD_ENTITY_QUERY,
    FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY,
    FIND_BY_IDEMPOTENCY_KEY_QUERY,
    FIND_ENTITY_QUERY,
    GET_FACT_QUERY,
    LIST_EVENTS_BY_SESSION_QUERY,
    LIST_INCIDENT_FACTS_QUERY,
    SAVE_ENTITY_PROFILE_QUERY,
    PURGE_USER_EPISODES_QUERY,
    PURGE_USER_EVENTS_QUERY,
    REVOKE_BY_USER_QUERY,
    REVOKE_FACT_QUERY,
    SEARCH_ENTITIES_PREFIX_QUERY,
    SEARCH_ENTITY_FULLTEXT_QUERY,
    SEARCH_ENTITY_VECTOR_QUERY,
    SEARCH_FACT_FULLTEXT_QUERY,
    SEARCH_FACT_VECTOR_QUERY,
    STORE_EPISODE_SUMMARY_QUERY,
    SUPERSEDE_FACT_QUERY,
    TRAVERSE_2HOP_QUERY,
    WRITE_FACT_QUERY,
)

logger = logging.getLogger("kryptodb.infrastructure.neo4j.store")


class StorageExecutionError(RuntimeError):
    """Raised when a Neo4j write or mutation fails or driver is disconnected."""


class SupersessionConflictError(RuntimeError):
    """Raised when an atomic supersession target is missing or already superseded."""


def _norm_entity_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split()).casefold()


class Neo4jMemoryStore(MemoryStore, EventStore):
    """Neo4j persistent store satisfying MemoryStore and EventStore."""

    def __init__(self, client: Any = None, embedder: Optional[Embedder] = None):
        self.client = client or neo4j_client
        self.embedder = embedder or embedding_service

    def _execute(self, query: str, *, raise_on_error: bool = False, **params) -> List[Dict[str, Any]]:
        if not self.client or not getattr(self.client, "driver", None):
            if raise_on_error:
                raise StorageExecutionError("Neo4j driver is not connected.")
            return []
        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(query, **params)
            else:
                records, _, _ = self.client.driver.execute_query(query, **params)
            return [dict(r) for r in records]
        except Exception as exc:
            logger.error("Neo4j query execution error: %s", exc)
            if raise_on_error:
                raise StorageExecutionError(f"Neo4j execution failed: {exc}") from exc
            return []

    def write_fact(self, fact: MemoryRecord, context: RequestContext) -> MemoryRecord:
        now = context.now.isoformat()
        desc = fact.description or f"{fact.subject} {fact.predicate} {fact.object}"

        # Vector embedding generation
        s_emb, s_pending = self.embedder.embed_with_resilience(fact.subject) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(fact.subject), False)
        o_emb, o_pending = self.embedder.embed_with_resilience(fact.object) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(fact.object), False)
        r_emb, r_pending = self.embedder.embed_with_resilience(desc) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(desc), False)

        params = {
            "s_name": fact.subject.strip(),
            "s_norm_name": _norm_entity_name(fact.subject),
            "s_id": f"ent-{uuid.uuid4().hex[:12]}",
            "s_emb": s_emb,
            "s_pending": s_pending,
            "o_name": fact.object.strip(),
            "o_norm_name": _norm_entity_name(fact.object),
            "o_id": f"ent-{uuid.uuid4().hex[:12]}",
            "o_emb": o_emb,
            "o_pending": o_pending,
            "fact_id": fact.id,
            "idempotency_key": fact.metadata.get("idempotency_key"),
            "predicate": fact.predicate.strip().upper(),
            "description": desc,
            "rel_emb": r_emb,
            "rel_pending": r_pending,
            "scope": fact.scope,
            "tenant_id": fact.tenant_id,
            "status": fact.status.value if hasattr(fact.status, "value") else str(fact.status),
            "version": fact.version,
            "confidence": float(fact.confidence),
            "trust_weight": float(fact.trust_weight),
            "valid_from": fact.valid_from.isoformat() if fact.valid_from else now,
            "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
            "source_agent_id": fact.source_agent_id,
            "dataset_id": fact.metadata.get("dataset_id"),
            "now": now,
        }

        idem_key = params.get("idempotency_key")
        if idem_key:
            existing_rows = self._execute(
                FIND_BY_IDEMPOTENCY_KEY_QUERY,
                raise_on_error=True,
                idempotency_key=idem_key,
                tenant_id=fact.tenant_id,
            )
            if existing_rows:
                row = existing_rows[0]
                r_props = dict(row.get("r", {})) if hasattr(row.get("r", {}), "items") else {}
                return self._record_to_memory(r_props, row.get("subject", fact.subject), row.get("object", fact.object))
        self._execute(WRITE_FACT_QUERY, raise_on_error=True, **params)
        return fact

    def get_fact(self, id: str, context: RequestContext) -> Optional[MemoryRecord]:
        filter_clause, params = FilterBuilder.build(context, alias="r")
        query = GET_FACT_QUERY.replace("{filter_clause}", filter_clause)
        records = self._execute(query, fact_id=id, **params)
        if not records:
            return None

        row = records[0]
        r = row.get("r", {})
        r_props = dict(r) if hasattr(r, "items") else (r if isinstance(r, dict) else {})
        return self._record_to_memory(r_props, row.get("subject", ""), row.get("object", ""))

    def find_active_by_subject_predicate(
        self, subject: str, predicate: str, context: RequestContext
    ) -> List[MemoryRecord]:
        filter_clause, params = FilterBuilder.build(context, alias="r")
        query = FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY.replace("{filter_clause}", filter_clause)
        records = self._execute(query, subject=subject, norm_subject=_norm_entity_name(subject), predicate=predicate.upper(), **params)

        results = []
        for row in records:
            r = row.get("r", {})
            r_props = dict(r) if hasattr(r, "items") else (r if isinstance(r, dict) else {})
            results.append(self._record_to_memory(r_props, row.get("subject", ""), row.get("object", "")))
        return results

    def supersede(self, old_id: str, new_id: str, context: RequestContext) -> None:
        now = context.now.isoformat()
        self._execute(SUPERSEDE_FACT_QUERY, old_id=old_id, new_id=new_id, tenant_id=context.tenant_id, now=now)

    def atomic_supersede(self, old_id: str, new_fact: MemoryRecord, context: RequestContext) -> MemoryRecord:
        now = context.now.isoformat()
        desc = new_fact.description or f"{new_fact.subject} {new_fact.predicate} {new_fact.object}"

        s_emb, s_pending = self.embedder.embed_with_resilience(new_fact.subject) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(new_fact.subject), False)
        o_emb, o_pending = self.embedder.embed_with_resilience(new_fact.object) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(new_fact.object), False)
        r_emb, r_pending = self.embedder.embed_with_resilience(desc) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(desc), False)

        params = {
            "old_id": old_id,
            "s_name": new_fact.subject.strip(),
            "s_norm_name": _norm_entity_name(new_fact.subject),
            "s_id": f"ent-{uuid.uuid4().hex[:12]}",
            "s_emb": s_emb,
            "s_pending": s_pending,
            "o_name": new_fact.object.strip(),
            "o_norm_name": _norm_entity_name(new_fact.object),
            "o_id": f"ent-{uuid.uuid4().hex[:12]}",
            "o_emb": o_emb,
            "o_pending": o_pending,
            "fact_id": new_fact.id,
            "idempotency_key": new_fact.metadata.get("idempotency_key"),
            "predicate": new_fact.predicate.strip().upper(),
            "description": desc,
            "rel_emb": r_emb,
            "rel_pending": r_pending,
            "scope": new_fact.scope,
            "tenant_id": new_fact.tenant_id,
            "status": new_fact.status.value if hasattr(new_fact.status, "value") else str(new_fact.status),
            "version": new_fact.version,
            "confidence": float(new_fact.confidence),
            "trust_weight": float(new_fact.trust_weight),
            "valid_from": new_fact.valid_from.isoformat() if new_fact.valid_from else now,
            "valid_until": new_fact.valid_until.isoformat() if new_fact.valid_until else None,
            "source_agent_id": new_fact.source_agent_id,
            "dataset_id": new_fact.metadata.get("dataset_id"),
            "now": now,
        }

        rows = self._execute(ATOMIC_SUPERSEDE_QUERY, raise_on_error=True, **params)
        if not rows:
            raise SupersessionConflictError(
                f"Supersession target '{old_id}' is missing, outside scope '{new_fact.scope}', or no longer active."
            )
        return new_fact

    def revoke(self, id: str, context: RequestContext) -> bool:
        now = context.now.isoformat()
        rows = self._execute(
            REVOKE_FACT_QUERY,
            raise_on_error=True,
            fact_id=id,
            tenant_id=context.tenant_id,
            is_system=bool(context.is_system or ("*" in context.active_scopes)),
            active_scopes=list(context.active_scopes),
            now=now,
        )
        return bool(rows)

    def search_vector(
        self, query_embedding: List[float], top_k: int, context: RequestContext
    ) -> List[ScoredItem]:
        filter_clause, params = FilterBuilder.build(context, alias="r")
        query = SEARCH_FACT_VECTOR_QUERY.replace("{filter_clause}", filter_clause)
        records = self._execute(query, top_k=top_k, query_embedding=query_embedding, **params)

        scored = []
        for row in records:
            r = row.get("r", {})
            r_props = dict(r) if hasattr(r, "items") else (r if isinstance(r, dict) else {})
            desc = r_props.get("description") or f"{row.get('subject')} {r_props.get('predicate')} {row.get('object')}"
            scored.append(
                ScoredItem(
                    item_id=r_props.get("id", ""),
                    score=float(row.get("score", 0.0)),
                    item_type="fact",
                    text=desc,
                    metadata={
                        "subject": row.get("subject", ""),
                        "predicate": r_props.get("predicate", ""),
                        "object": row.get("object", ""),
                        "scope": r_props.get("scope", "default"),
                    },
                )
            )
        return scored

    def search_fulltext(
        self, query_text: str, top_k: int, context: RequestContext
    ) -> List[ScoredItem]:
        filter_clause, params = FilterBuilder.build(context, alias="r")
        query = SEARCH_FACT_FULLTEXT_QUERY.replace("{filter_clause}", filter_clause)
        records = self._execute(query, query_text=query_text, **params)

        scored = []
        for row in records:
            r = row.get("r", {})
            r_props = dict(r) if hasattr(r, "items") else (r if isinstance(r, dict) else {})
            desc = r_props.get("description") or f"{row.get('subject')} {r_props.get('predicate')} {row.get('object')}"
            scored.append(
                ScoredItem(
                    item_id=r_props.get("id", ""),
                    score=float(row.get("score", 0.0)),
                    item_type="fact",
                    text=desc,
                    metadata={
                        "subject": row.get("subject", ""),
                        "predicate": r_props.get("predicate", ""),
                        "object": row.get("object", ""),
                        "scope": r_props.get("scope", "default"),
                    },
                )
            )
        return scored

    def list_incident_facts(
        self, subject: str, context: RequestContext, limit: int = 25
    ) -> List[Dict[str, Any]]:
        filter_clause, params = FilterBuilder.build(context, alias="r")
        query = LIST_INCIDENT_FACTS_QUERY.replace("{filter_clause}", filter_clause)
        rows = self._execute(
            query,
            norm_subject=_norm_entity_name(subject),
            limit=limit,
            **params,
        )
        facts = []
        for row in rows:
            props = row.get("r", {})
            props = dict(props) if hasattr(props, "items") else (props if isinstance(props, dict) else {})
            facts.append({
                "id": props.get("id", ""),
                "subject": row.get("subject", subject),
                "predicate": props.get("predicate", ""),
                "object": row.get("object", ""),
                "description": props.get("description"),
                "status": props.get("status", "active"),
                "valid_from": props.get("valid_from"),
            })
        return facts

    def save_entity_profile(
        self,
        entity_id: str,
        scope: str,
        profile_payload: Dict[str, Any],
        context: RequestContext,
    ) -> None:
        profile = profile_payload.get("profile")
        if hasattr(profile, "model_dump"):
            profile = profile.model_dump(mode="json")
        self._execute(
            SAVE_ENTITY_PROFILE_QUERY,
            entity_name=entity_id,
            tenant_id=context.tenant_id,
            profile_json=json.dumps(profile if profile is not None else profile_payload, sort_keys=True),
            now=context.now.isoformat(),
        )

    def find_entity(self, name: str, context: RequestContext) -> Optional[EntityRecord]:
        records = self._execute(FIND_ENTITY_QUERY, name=name, tenant_id=context.tenant_id)
        if not records:
            return None
        row = records[0]
        return EntityRecord(
            id=row.get("id", ""),
            name=row.get("name", ""),
            tenant_id=row.get("tenant_id", context.tenant_id),
            created_at=context.now,
            embedding=row.get("embedding"),
        )

    def search_entities(
        self, query: str, context: RequestContext, limit: int = 20
    ) -> List[EntityRecord]:
        clean_q = query.strip()
        prefix = clean_q[:3] if len(clean_q) >= 3 else clean_q
        try:
            records = self._execute(
                SEARCH_ENTITIES_PREFIX_QUERY,
                prefix=prefix,
                tenant_id=context.tenant_id,
                limit=limit,
            )
        except Exception as exc:
            logger.debug("search_entities failed: %s", exc)
            records = []
        return [
            EntityRecord(
                id=r.get("id", ""),
                name=r.get("name", ""),
                tenant_id=r.get("tenant_id", context.tenant_id),
                created_at=context.now,
                embedding=r.get("embedding"),
            )
            for r in records
        ]

    def add_entity(
        self, id: str, name: str, context: RequestContext, metadata: Optional[Dict[str, Any]] = None
    ) -> EntityRecord:
        emb = self.embedder.embed(name) if self.embedder else None
        self._execute(
            ADD_ENTITY_QUERY,
            id=id,
            name=name,
            tenant_id=context.tenant_id,
            embedding=emb,
            now=context.now.isoformat(),
        )
        return EntityRecord(
            id=id,
            name=name,
            tenant_id=context.tenant_id,
            created_at=context.now,
            embedding=emb,
            metadata=metadata or {},
        )

    def revoke_by_user(self, user_id: str, context: RequestContext) -> int:
        active_scopes = list(context.active_scopes)
        records = self._execute(
            REVOKE_BY_USER_QUERY,
            raise_on_error=True,
            tenant_id=context.tenant_id,
            user_id=user_id,
            now=context.now.isoformat(),
            active_scopes=active_scopes,
            is_system=bool(context.is_system),
        )
        self._execute(
            PURGE_USER_EVENTS_QUERY,
            raise_on_error=True,
            tenant_id=context.tenant_id,
            user_id=user_id,
            active_scopes=active_scopes,
            is_system=bool(context.is_system),
        )
        self._execute(
            PURGE_USER_EPISODES_QUERY,
            raise_on_error=True,
            tenant_id=context.tenant_id,
            user_id=user_id,
            active_scopes=active_scopes,
            is_system=bool(context.is_system),
        )
        if records:
            return int(records[0].get("revoked_count", 0))
        return 0

    def propose_atomic(
        self,
        subject_norm: str,
        predicate: str,
        object_norm: str,
        new_fact: MemoryRecord,
        context: RequestContext,
        is_explicit_mutation: bool = False,
    ) -> MemoryRecord:
        """Single-transaction atomic check-and-write for Cardinality.ONE predicates."""
        now = context.now.isoformat()
        desc = new_fact.description or f"{new_fact.subject} {new_fact.predicate} {new_fact.object}"

        s_emb, s_pending = self.embedder.embed_with_resilience(new_fact.subject) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(new_fact.subject), False)
        o_emb, o_pending = self.embedder.embed_with_resilience(new_fact.object) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(new_fact.object), False)
        r_emb, r_pending = self.embedder.embed_with_resilience(desc) if hasattr(self.embedder, "embed_with_resilience") else (self.embedder.embed(desc), False)

        params = {
            "s_name": new_fact.subject.strip(),
            "s_norm_name": subject_norm,
            "s_id": f"ent-{uuid.uuid4().hex[:12]}",
            "s_emb": s_emb,
            "s_pending": s_pending,
            "o_name": new_fact.object.strip(),
            "o_norm_name": object_norm,
            "o_id": f"ent-{uuid.uuid4().hex[:12]}",
            "o_emb": o_emb,
            "o_pending": o_pending,
            "fact_id": new_fact.id,
            "idempotency_key": new_fact.metadata.get("idempotency_key"),
            "predicate": predicate.strip().upper(),
            "description": desc,
            "rel_emb": r_emb,
            "rel_pending": r_pending,
            "scope": new_fact.scope,
            "tenant_id": new_fact.tenant_id,
            "version": new_fact.version,
            "confidence": float(new_fact.confidence),
            "trust_weight": float(new_fact.trust_weight),
            "valid_from": new_fact.valid_from.isoformat() if new_fact.valid_from else now,
            "valid_until": new_fact.valid_until.isoformat() if new_fact.valid_until else None,
            "source_agent_id": new_fact.source_agent_id,
            "dataset_id": new_fact.metadata.get("dataset_id"),
            "now": now,
            "is_explicit_mutation": bool(is_explicit_mutation),
        }
        rows = self._execute(ATOMIC_WRITE_CARDINALITY_ONE_QUERY, raise_on_error=True, **params)
        if rows:
            row = rows[0]
            r_node = row.get("r")
            if r_node is not None and hasattr(r_node, "get"):
                fact_id = r_node.get("id", new_fact.id)
                status_val = r_node.get("status", new_fact.status.value)
                version_val = r_node.get("version", new_fact.version)
                return new_fact.model_copy(
                    update={
                        "id": fact_id,
                        "status": MemoryStatus(status_val) if status_val in [s.value for s in MemoryStatus] else new_fact.status,
                        "version": int(version_val) if version_val is not None else new_fact.version,
                    }
                )
        return new_fact

    def traverse_2hop(
        self, seed_names: List[str], context: RequestContext, max_results: int = 25
    ) -> List[PathResult]:
        filter_clause_r1, params1 = FilterBuilder.build(context, alias="r1")
        filter_clause_r2, params2 = FilterBuilder.build(context, alias="r2")
        query = TRAVERSE_2HOP_QUERY.replace(
            "{filter_clause_r1}", filter_clause_r1
        ).replace(
            "{filter_clause_r2}", filter_clause_r2
        )

        merged_params = {**params1, **params2, "seed_names": seed_names, "max_results": max_results}
        records = self._execute(query, **merged_params)

        paths = []
        for row in records:
            seed = row.get("seed_name", "")
            hop1 = row.get("hop1_name", "")
            hop2 = row.get("hop2_name")
            r1 = dict(row.get("r1", {})) if hasattr(row.get("r1", {}), "items") else {}
            r2 = dict(row.get("r2", {})) if row.get("r2") and hasattr(row.get("r2", {}), "items") else None

            step1 = PathStep(
                hop=1,
                source=seed,
                predicate=r1.get("predicate", ""),
                target=hop1,
                edge_id=r1.get("id", ""),
                confidence=float(r1.get("confidence", 1.0)),
            )

            if hop2 and r2:
                step2 = PathStep(
                    hop=2,
                    source=hop1,
                    predicate=r2.get("predicate", ""),
                    target=hop2,
                    edge_id=r2.get("id", ""),
                    confidence=float(r2.get("confidence", 1.0)),
                )
                score = step1.confidence * step2.confidence
                paths.append(
                    PathResult(
                        seed=seed,
                        target=hop2,
                        hops=2,
                        score=score,
                        steps=[step1, step2],
                        fact_ids=[step1.edge_id, step2.edge_id],
                    )
                )
            else:
                paths.append(
                    PathResult(
                        seed=seed,
                        target=hop1,
                        hops=1,
                        score=step1.confidence,
                        steps=[step1],
                        fact_ids=[step1.edge_id],
                    )
                )
        return paths

    def append(self, event: Event, context: RequestContext) -> str:
        self._execute(
            APPEND_EVENT_QUERY,
            id=event.id,
            session_id=event.session_id,
            event_type=event.event_type,
            content=event.content,
            tenant_id=context.tenant_id,
            scope=event.scope,
            agent_id=event.agent_id,
            user_id=event.user_id,
            timestamp=event.timestamp.isoformat(),
        )
        return event.id

    def list_by_session(self, session_id: str, context: RequestContext) -> List[Event]:
        records = self._execute(
            LIST_EVENTS_BY_SESSION_QUERY,
            session_id=session_id,
            tenant_id=context.tenant_id,
        )
        events = []
        for r in records:
            ev = dict(r.get("ev", {})) if hasattr(r.get("ev", {}), "items") else {}
            events.append(
                Event(
                    id=ev.get("id", ""),
                    session_id=ev.get("session_id", session_id),
                    event_type=ev.get("event_type", "user_message"),
                    content=ev.get("content", ""),
                    tenant_id=ev.get("tenant_id", context.tenant_id),
                    scope=ev.get("scope", "default"),
                    agent_id=ev.get("agent_id"),
                    user_id=ev.get("user_id"),
                )
            )
        return events

    def store_summary(self, summary: EpisodeSummary, context: RequestContext) -> str:
        self._execute(
            STORE_EPISODE_SUMMARY_QUERY,
            id=summary.id,
            session_id=summary.session_id,
            summary=summary.summary,
            topics=summary.topics,
            tenant_id=context.tenant_id,
            scope=summary.scope,
            timestamp=summary.timestamp.isoformat(),
        )
        return summary.id

    def _record_to_memory(self, props: Dict[str, Any], subject: str, obj: str) -> MemoryRecord:
        status_val = props.get("status", "active")
        try:
            status = MemoryStatus(status_val)
        except ValueError:
            status = MemoryStatus.ACTIVE

        return MemoryRecord(
            id=props.get("id", str(uuid.uuid4())),
            subject=subject,
            predicate=props.get("predicate", ""),
            object=obj,
            description=props.get("description"),
            scope=props.get("scope", "default"),
            tenant_id=props.get("tenant_id", "default"),
            status=status,
            version=int(props.get("version", 1)),
            confidence=float(props.get("confidence", 1.0)),
            trust_weight=float(props.get("trust_weight", 1.0)),
            source_agent_id=props.get("source_agent_id", "agent-1"),
        )
