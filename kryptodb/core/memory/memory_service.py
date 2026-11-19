"""Unified memory service: proposes, validates, lifecycle state transitions, and user controls."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kryptodb.core.context import RequestContext, create_system_context
from kryptodb.core.memory.conflicts import ConflictAction, ConflictEngine
from kryptodb.core.memory.lifecycle import transition
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus, MemoryType
from kryptodb.core.memory.predicates import predicate_registry
from kryptodb.core.memory.write_policy import validate_proposal
from kryptodb.core.ports import EventStore, MemoryStore
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.store import Neo4jMemoryStore

logger = logging.getLogger("kryptodb.core.memory.service")


class MemoryService:
    """Unified application memory service managing durable semantic graph facts."""

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        event_store: Optional[EventStore] = None,
        conflict_engine: Optional[ConflictEngine] = None,
        client: Any = None,
    ):
        self.client = client or neo4j_client
        neo_store = Neo4jMemoryStore(client=self.client) if not store else store
        self.store: MemoryStore = store or neo_store
        self.event_store: Optional[EventStore] = event_store or (neo_store if isinstance(neo_store, EventStore) else None)
        self.conflict_engine: ConflictEngine = conflict_engine or ConflictEngine(self.store)

    def propose_memory(
        self,
        subject: str,
        predicate: str,
        obj: str,
        context: RequestContext,
        confidence: float = 1.0,
        description: Optional[str] = None,
        scope: str = "default",
        source_event_id: Optional[str] = None,
        quote_substring: Optional[str] = None,
        session_id: Optional[str] = None,
        bypass_conflict_check: bool = False,
    ) -> MemoryRecord:
        """Core Verb: Propose a fact with validation, quotation check, and conflict supersession."""
        # 1. Enforce write policy and quotation substring check
        validate_proposal(
            subject=subject,
            predicate=predicate,
            obj=obj,
            confidence=confidence,
            context=context,
            source_event_id=source_event_id,
            quote_substring=quote_substring,
            event_store=self.event_store,
            session_id=session_id or context.session_id,
        )

        norm_pred = predicate_registry.normalize(predicate)
        version = 1
        previous_version_id = None
        status = MemoryStatus.ACTIVE

        # 2. Conflict evaluation
        if not bypass_conflict_check:
            eval_res = self.conflict_engine.evaluate(
                subject=subject,
                predicate=norm_pred,
                obj=obj,
                confidence=confidence,
                context=context,
                store=self.store,
            )

            if eval_res.action == ConflictAction.REFRESH_EXISTING:
                logger.info("Fact (%s, %s, %s) is an existing duplicate; refreshing.", subject, norm_pred, obj)
                existing = self.store.get_fact(eval_res.existing_fact_id, context)
                if existing:
                    return existing
                # Fallback to creating if lookup failed
                version = eval_res.version

            elif eval_res.action == ConflictAction.SUPERSEDE_AND_CREATE:
                old_id = eval_res.supersede_fact_id
                previous_version_id = old_id
                version = eval_res.version

        effective_scope = scope
        if scope == "default" and context.active_scopes and "*" not in context.active_scopes:
            effective_scope = next(iter(context.active_scopes))

        import hashlib
        idempotency_key = hashlib.sha256(
            f"{context.tenant_id.lower()}:{effective_scope.lower()}:{subject.strip().lower()}:{norm_pred}:{obj.strip().lower()}".encode("utf-8")
        ).hexdigest()

        new_record = MemoryRecord(
            id=f"fact-{uuid.uuid4().hex[:12]}",
            subject=subject.strip(),
            predicate=norm_pred,
            object=obj.strip(),
            description=description or f"{subject.strip()} {norm_pred} {obj.strip()}",
            scope=effective_scope,
            tenant_id=context.tenant_id,
            status=status,
            version=version,
            previous_version_id=previous_version_id,
            confidence=confidence,
            valid_from=context.now,
            source_agent_id=context.agent_id,
            metadata={
                "source_event_id": source_event_id,
                "quote_substring": quote_substring,
                "session_id": session_id or context.session_id,
                "idempotency_key": idempotency_key,
            },
        )

        if previous_version_id:
            logger.info("Atomically superseding existing fact %s with new fact %s.", previous_version_id, new_record.id)
            if hasattr(self.store, "atomic_supersede"):
                return self.store.atomic_supersede(previous_version_id, new_record, context)
            self.store.supersede(previous_version_id, new_record.id, context)

        return self.store.write_fact(new_record, context)

    def write_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        description: Optional[str] = None,
        scope: str = "default",
        tenant_id: str = "default",
        confidence: float = 1.0,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
        source_agent_id: str = "agent-1",
        dataset_id: Optional[str] = None,
        bypass_conflict_check: bool = False,
    ) -> MemoryRecord:
        """Backward-compatible write helper creating context internally."""
        ctx = RequestContext.create(
            tenant_id=tenant_id,
            agent_id=source_agent_id,
            active_scopes=[scope],
            now=valid_from,
        )
        return self.propose_memory(
            subject=subject,
            predicate=predicate,
            obj=obj,
            context=ctx,
            confidence=confidence,
            description=description,
            scope=scope,
            bypass_conflict_check=bypass_conflict_check,
        )

    def get_memory(self, id: str, context: RequestContext) -> Optional[MemoryRecord]:
        """User control: fetch single memory by id."""
        return self.store.get_fact(id, context)

    def list_memories(
        self,
        context: RequestContext,
        user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """User control: list memories belonging to the context/user."""
        # Query active facts across active scopes
        if hasattr(self.store, "search_fulltext"):
            items = self.store.search_fulltext("", top_k=limit + offset, context=context)
            fact_ids = [it.item_id for it in items if it.item_type == "fact"]
            records = []
            for fid in fact_ids[offset:offset + limit]:
                rec = self.store.get_fact(fid, context)
                if rec:
                    records.append(rec)
            return records
        return []

    def correct_memory(
        self,
        id: str,
        new_value: str,
        context: RequestContext,
    ) -> MemoryRecord:
        """User control: correct a fact by superseding it with a new object value."""
        old_fact = self.store.get_fact(id, context)
        if not old_fact:
            raise ValueError(f"Memory with id '{id}' not found or inaccessible.")

        new_fact_id = f"fact-{uuid.uuid4().hex[:12]}"
        self.store.supersede(id, new_fact_id, context)

        new_record = old_fact.model_copy(
            update={
                "id": new_fact_id,
                "object": new_value.strip(),
                "description": f"{old_fact.subject} {old_fact.predicate} {new_value.strip()}",
                "version": old_fact.version + 1,
                "previous_version_id": old_fact.id,
                "status": MemoryStatus.ACTIVE,
                "valid_from": context.now,
                "valid_until": None,
            }
        )
        return self.store.write_fact(new_record, context)

    def forget_memory(self, id: str, context: RequestContext) -> None:
        """User control: permanently revoke a single memory."""
        self.store.revoke(id, context)

    def forget_user(self, user_id: str, context: RequestContext) -> int:
        """User control (GDPR/privacy): permanently revoke all memories and events for a user."""
        return self.store.revoke_by_user(user_id, context)


    def supersede_fact(self, old_fact_id: str, tenant_id: str = "default") -> None:
        """Compatibility helper for superseding facts."""
        ctx = create_system_context(tenant_id=tenant_id)
        self.store.supersede(old_fact_id, f"superseded-by-{uuid.uuid4().hex[:8]}", ctx)

    def revoke_fact(self, fact_id: str, tenant_id: str = "default") -> bool:
        """Compatibility helper for revoking facts."""
        ctx = create_system_context(tenant_id=tenant_id)
        self.store.revoke(fact_id, ctx)
        return True


memory_service = MemoryService()
