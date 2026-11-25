"""Phase 1 EncryptedNeo4jMemoryStore (v3 thin-slice spec).

Store invariant: ZERO plaintext knowledge fields are ever written to Neo4j.
Every blind-index match is verified in daemon RAM after AES-256-GCM decryption
and Section 5 integrity checks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.infrastructure.crypto.blind_index import BlindIndex, normalize
from kryptodb.infrastructure.crypto.envelope import (
    DecryptionKeyError,
    EnvelopeCipher,
    IntegrityMismatchError,
)
from kryptodb.infrastructure.crypto.keyring import LocalKeyFileProvider
from kryptodb.infrastructure.neo4j.encrypted_queries import (
    ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY,
    CREATE_ENCRYPTED_ENTITY_QUERY,
    ENCRYPTED_SCHEMA_DDL,
    FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY,
    FIND_ENTITIES_BY_BIDX_QUERY,
    FIND_IDEMPOTENCY_KEY_QUERY,
    FORGET_ENCRYPTED_FACT_QUERY,
    GET_ENCRYPTED_FACT_QUERY,
    WRITE_ENCRYPTED_FACT_QUERY,
)
class StorageExecutionError(RuntimeError):
    """Raised when a Neo4j write or mutation fails or driver is disconnected."""


class SupersessionConflictError(RuntimeError):
    """Raised when an atomic supersession target is missing or already superseded."""

logger = logging.getLogger("kryptodb.infrastructure.neo4j.encrypted_store")


class EncryptedNeo4jMemoryStore:
    """Neo4j store enforcing envelope encryption, collision-safe entities, and tamper detection."""

    def __init__(
        self,
        client: Any,
        key_provider: LocalKeyFileProvider,
        field_hex_len: int = 24,
        token_hex_len: int = 16,
        vector_index: Any = None,
        embed_fn: Optional[Callable[[str], List[float]]] = None,
        audit_ledger: Any = None,
    ) -> None:
        self.client = client
        self.key_provider = key_provider
        self.cipher = EnvelopeCipher(key_provider)
        self.blind_index = BlindIndex(
            key_provider.bundle.index_key,
            field_hex_len=field_hex_len,
            token_hex_len=token_hex_len,
        )
        self.vector_index = vector_index
        self.embed_fn = embed_fn
        self.audit_ledger = audit_ledger
        self.integrity_failure_count = 0
        self.decrypt_failure_count = 0

    def _sync_vector_upsert(self, scope: str, fact_id: str, text: str) -> None:
        if self.vector_index is not None and self.embed_fn is not None and text.strip():
            self.vector_index.upsert(scope, fact_id, self.embed_fn(text))

    def _sync_vector_remove(self, scope: str, fact_id: str) -> None:
        if self.vector_index is not None:
            self.vector_index.remove(scope, fact_id)

    def ensure_schema(self) -> None:
        if not self.client or not getattr(self.client, "driver", None):
            raise StorageExecutionError("Neo4j driver is not connected.")
        with self.client.driver.session() as session:
            for ddl in ENCRYPTED_SCHEMA_DDL:
                session.run(ddl)

    def _run_query(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        if not self.client or not getattr(self.client, "driver", None):
            raise StorageExecutionError("Neo4j driver is not connected.")
        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(query, **params)
            else:
                records, _, _ = self.client.driver.execute_query(query, **params)
            return [dict(r) for r in records]
        except Exception as exc:
            raise StorageExecutionError(f"Neo4j encrypted query failed: {exc}") from exc

    def resolve_or_create_entity(self, name: str, scope: str, now_iso: str) -> str:
        """Collision-safe entity resolution: reuses UUID only on exact decrypted normalized match."""
        name_bidx = self.blind_index.compute("entity", name)
        target_norm = normalize("entity", name)

        candidates = self._run_query(FIND_ENTITIES_BY_BIDX_QUERY, scope=scope, name_bidx=name_bidx)
        for cand in candidates:
            try:
                decrypted = self.cipher.open_entity_name(
                    scope=scope,
                    entity_id=cand["id"],
                    name_ct=cand["name_ct"],
                    wrapped_dek=cand["wrapped_dek"],
                    stored_name_bidx=cand["name_bidx"],
                    blind_index=self.blind_index,
                )
            except IntegrityMismatchError:
                self.integrity_failure_count += 1
                raise
            except DecryptionKeyError:
                self.decrypt_failure_count += 1
                raise
            if normalize("entity", decrypted) == target_norm:
                return str(cand["id"])

        entity_id = f"ent-{uuid.uuid4().hex[:12]}"
        sealed = self.cipher.seal_entity_name(scope=scope, entity_id=entity_id, name=name.strip())
        self._run_query(
            CREATE_ENCRYPTED_ENTITY_QUERY,
            id=entity_id,
            scope=scope,
            name_bidx=name_bidx,
            name_ct=sealed["name_ct"],
            wrapped_dek=sealed["wrapped_dek"],
            kid=sealed["kid"],
            now=now_iso,
        )
        return entity_id

    def write_fact(self, fact: MemoryRecord, context: RequestContext) -> MemoryRecord:
        now_iso = context.now.isoformat()
        scope = fact.scope
        desc = fact.description or f"{fact.subject} {fact.predicate} {fact.object}"
        turn_id = str(fact.metadata.get("source_turn_id") or fact.metadata.get("source_event_id") or "")
        idem_key = self.blind_index.compute_idempotency_key(scope, fact.subject, fact.predicate, fact.object, turn_id)

        existing = self._run_query(FIND_IDEMPOTENCY_KEY_QUERY, scope=scope, idempotency_key=idem_key)
        if existing:
            found = self.get_fact(existing[0]["fact_id"], context, scope=scope)
            if found is not None:
                return found

        s_id = self.resolve_or_create_entity(fact.subject, scope=scope, now_iso=now_iso)
        o_id = self.resolve_or_create_entity(fact.object, scope=scope, now_iso=now_iso)

        valid_from_iso = fact.valid_from.isoformat() if fact.valid_from else now_iso
        valid_until_iso = fact.valid_until.isoformat() if fact.valid_until else None
        status_str = fact.status.value if hasattr(fact.status, "value") else str(fact.status)

        sealed = self.cipher.seal_fact(
            fact_id=fact.id,
            scope=scope,
            version=fact.version,
            status=status_str,
            subject=fact.subject.strip(),
            predicate=fact.predicate.strip().upper(),
            obj=fact.object.strip(),
            description=desc,
            valid_from=valid_from_iso,
            valid_until=valid_until_iso,
            confidence=float(fact.confidence),
            trust_weight=float(fact.trust_weight),
            metadata=fact.metadata,
        )

        self._run_query(
            WRITE_ENCRYPTED_FACT_QUERY,
            subject_entity_id=s_id,
            object_entity_id=o_id,
            idempotency_key=idem_key,
            fact_id=fact.id,
            scope=scope,
            status=status_str,
            version=int(fact.version),
            valid_from=valid_from_iso,
            valid_until=valid_until_iso,
            confidence=float(fact.confidence),
            trust_weight=float(fact.trust_weight),
            subject_bidx=self.blind_index.compute("subject", fact.subject),
            predicate_bidx=self.blind_index.compute("predicate", fact.predicate),
            object_bidx=self.blind_index.compute("object", fact.object),
            search_tokens=self.blind_index.extract_search_tokens(fact.subject, fact.predicate, fact.object, desc),
            knowledge_ct=sealed["knowledge_ct"],
            wrapped_dek=sealed["wrapped_dek"],
            kid=sealed["kid"],
            now=now_iso,
        )
        self._sync_vector_upsert(scope, fact.id, desc)
        return fact

    def get_fact(self, fact_id: str, context: RequestContext, scope: Optional[str] = None) -> Optional[MemoryRecord]:
        target_scope = scope or (next(iter(context.active_scopes)) if context.active_scopes else "default")
        rows = self._run_query(GET_ENCRYPTED_FACT_QUERY, fact_id=fact_id, scope=target_scope)
        if not rows:
            return None
        return self._decrypt_edge(rows[0]["props"])

    def find_active_by_subject_predicate(
        self,
        subject: str,
        predicate: str,
        context: RequestContext,
        scope: Optional[str] = None,
    ) -> List[MemoryRecord]:
        target_scope = scope or (next(iter(context.active_scopes)) if context.active_scopes else "default")
        subject_bidx = self.blind_index.compute("subject", subject)
        predicate_bidx = self.blind_index.compute("predicate", predicate)
        rows = self._run_query(
            FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY,
            scope=target_scope,
            subject_bidx=subject_bidx,
            predicate_bidx=predicate_bidx,
            now=context.now.isoformat(),
        )
        if not rows:
            return []

        norm_s = normalize("subject", subject)
        norm_p = normalize("predicate", predicate)
        matched: List[MemoryRecord] = []
        decrypt_errors = 0
        for row in rows:
            try:
                rec = self._decrypt_edge(row["props"])
            except DecryptionKeyError as exc:
                decrypt_errors += 1
                if "Missing encryption key" in str(exc):
                    raise
                continue
            if normalize("subject", rec.subject) == norm_s and normalize("predicate", rec.predicate) == norm_p:
                matched.append(rec)

        if decrypt_errors > 0 and (decrypt_errors / len(rows)) > 0.05:
            raise DecryptionKeyError(
                f"Batch decryption failure rate exceeded 5% ({decrypt_errors}/{len(rows)} candidates failed)"
            )
        return matched

    def atomic_supersede(
        self,
        old_fact: MemoryRecord,
        new_fact: MemoryRecord,
        context: RequestContext,
    ) -> MemoryRecord:
        """Atomically closes old_fact (rewriting its encrypted payload as superseded) and creates new_fact."""
        now_iso = context.now.isoformat()
        scope = new_fact.scope
        old_valid_from = old_fact.valid_from.isoformat() if old_fact.valid_from else now_iso

        # Section 5: rewrite closed edge's payload with status='superseded' and valid_until=now_iso
        closed_sealed = self.cipher.seal_fact(
            fact_id=old_fact.id,
            scope=old_fact.scope,
            version=old_fact.version,
            status="superseded",
            subject=old_fact.subject.strip(),
            predicate=old_fact.predicate.strip().upper(),
            obj=old_fact.object.strip(),
            description=old_fact.description or f"{old_fact.subject} {old_fact.predicate} {old_fact.object}",
            valid_from=old_valid_from,
            valid_until=now_iso,
            confidence=float(old_fact.confidence),
            trust_weight=float(old_fact.trust_weight),
            metadata=old_fact.metadata,
        )

        s_id = self.resolve_or_create_entity(new_fact.subject, scope=scope, now_iso=now_iso)
        o_id = self.resolve_or_create_entity(new_fact.object, scope=scope, now_iso=now_iso)
        new_desc = new_fact.description or f"{new_fact.subject} {new_fact.predicate} {new_fact.object}"
        new_valid_from = new_fact.valid_from.isoformat() if new_fact.valid_from else now_iso
        turn_id = str(new_fact.metadata.get("source_turn_id") or "")
        idem_key = self.blind_index.compute_idempotency_key(
            scope, new_fact.subject, new_fact.predicate, new_fact.object, turn_id
        )

        new_sealed = self.cipher.seal_fact(
            fact_id=new_fact.id,
            scope=scope,
            version=new_fact.version,
            status="active",
            subject=new_fact.subject.strip(),
            predicate=new_fact.predicate.strip().upper(),
            obj=new_fact.object.strip(),
            description=new_desc,
            valid_from=new_valid_from,
            valid_until=None,
            confidence=float(new_fact.confidence),
            trust_weight=float(new_fact.trust_weight),
            metadata=new_fact.metadata,
        )

        rows = self._run_query(
            ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY,
            old_id=old_fact.id,
            scope=scope,
            expected_version=int(old_fact.version),
            closed_knowledge_ct=closed_sealed["knowledge_ct"],
            closed_wrapped_dek=closed_sealed["wrapped_dek"],
            closed_kid=closed_sealed["kid"],
            subject_entity_id=s_id,
            object_entity_id=o_id,
            idempotency_key=idem_key,
            fact_id=new_fact.id,
            version=int(new_fact.version),
            valid_from=new_valid_from,
            confidence=float(new_fact.confidence),
            trust_weight=float(new_fact.trust_weight),
            subject_bidx=self.blind_index.compute("subject", new_fact.subject),
            predicate_bidx=self.blind_index.compute("predicate", new_fact.predicate),
            object_bidx=self.blind_index.compute("object", new_fact.object),
            search_tokens=self.blind_index.extract_search_tokens(
                new_fact.subject, new_fact.predicate, new_fact.object, new_desc
            ),
            knowledge_ct=new_sealed["knowledge_ct"],
            wrapped_dek=new_sealed["wrapped_dek"],
            kid=new_sealed["kid"],
            now=now_iso,
        )
        if not rows:
            raise SupersessionConflictError(
                f"Atomic supersede failed: fact '{old_fact.id}' (version {old_fact.version}) is missing or no longer active."
            )
        self._sync_vector_remove(old_fact.scope, old_fact.id)
        self._sync_vector_upsert(scope, new_fact.id, new_desc)
        return new_fact

    def forget_fact(self, fact_id: str, context: RequestContext, scope: Optional[str] = None) -> bool:
        target_scope = scope or (next(iter(context.active_scopes)) if context.active_scopes else "default")
        rows = self._run_query(
            FORGET_ENCRYPTED_FACT_QUERY,
            fact_id=fact_id,
            scope=target_scope,
            now=context.now.isoformat(),
        )
        if rows:
            self._sync_vector_remove(target_scope, fact_id)
            if self.audit_ledger is not None:
                self.audit_ledger.append(
                    actor=context.agent_id,
                    action="forget_encrypted_fact",
                    tenant_id=context.tenant_id,
                    scope=target_scope,
                    outcome="allowed",
                    target_id=fact_id,
                )
        return bool(rows)

    def forget_by_subject_predicate(
        self,
        subject: str,
        predicate: str,
        context: RequestContext,
        scope: Optional[str] = None,
    ) -> List[str]:
        """Collision-safe logical deletion: decrypt-and-verify exact (subject, predicate) before revoking."""
        target_scope = scope or (next(iter(context.active_scopes)) if context.active_scopes else "default")
        exact_matches = self.find_active_by_subject_predicate(subject, predicate, context, scope=target_scope)
        forgotten_ids: List[str] = []
        for rec in exact_matches:
            if self.forget_fact(rec.id, context, scope=target_scope):
                forgotten_ids.append(rec.id)
        return forgotten_ids

    def _decrypt_edge(self, props: Dict[str, Any]) -> MemoryRecord:
        try:
            payload = self.cipher.open_and_verify_fact(props, self.blind_index)
        except IntegrityMismatchError:
            self.integrity_failure_count += 1
            logger.error("kryptodb_integrity_failure_total incremented for fact '%s'", props.get("id"))
            raise
        except DecryptionKeyError:
            self.decrypt_failure_count += 1
            logger.error("kryptodb_decrypt_failure_total incremented for fact '%s'", props.get("id"))
            raise

        return MemoryRecord(
            id=str(props["id"]),
            subject=str(payload["subject"]),
            predicate=str(payload["predicate"]),
            object=str(payload["object"]),
            description=str(payload.get("description", "")),
            scope=str(payload["scope"]),
            tenant_id="default",
            status=MemoryStatus(payload["status"]),
            version=int(payload["version"]),
            confidence=float(payload["confidence"]),
            trust_weight=float(payload["trust_weight"]),
            metadata=dict(payload.get("metadata") or {}),
        )
