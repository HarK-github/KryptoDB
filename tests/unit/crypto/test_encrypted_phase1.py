"""Phase 1 Release-Gate Tests for KryptoDB v3 Encrypted Memory."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.infrastructure.crypto import (
    BlindIndex,
    DecryptionKeyError,
    EnvelopeCipher,
    IntegrityMismatchError,
    LocalKeyFileProvider,
    generate_key_file,
    normalize,
)
from kryptodb.infrastructure.neo4j.encrypted_queries import (
    ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY,
    CREATE_ENCRYPTED_ENTITY_QUERY,
    FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY,
    FIND_ENTITIES_BY_BIDX_QUERY,
    FIND_IDEMPOTENCY_KEY_QUERY,
    FORGET_ENCRYPTED_FACT_QUERY,
    GET_ENCRYPTED_FACT_QUERY,
    WRITE_ENCRYPTED_FACT_QUERY,
)
from kryptodb.infrastructure.neo4j.encrypted_store import EncryptedNeo4jMemoryStore
from kryptodb.infrastructure.neo4j.encrypted_store import SupersessionConflictError


class InMemoryEncryptedNeo4jMock:
    """In-memory stub simulating Neo4j's encrypted Cypher queries for Phase 1 unit gates."""

    def __init__(self) -> None:
        self.driver = object()
        self.entities: List[Dict[str, Any]] = []
        self.facts: Dict[str, Dict[str, Any]] = {}
        self.idempotency_keys: Dict[tuple[str, str], str] = {}
        self.captured_write_params: List[Dict[str, Any]] = []

    def execute_query_with_retry(self, query: str, **params: Any):
        if query == FIND_ENTITIES_BY_BIDX_QUERY:
            rows = [
                e for e in self.entities
                if e["scope"] == params["scope"] and e["name_bidx"] == params["name_bidx"]
            ]
            return rows, None, None

        if query == CREATE_ENCRYPTED_ENTITY_QUERY:
            self.captured_write_params.append(dict(params))
            self.entities.append(dict(params))
            return [{"id": params["id"]}], None, None

        if query == FIND_IDEMPOTENCY_KEY_QUERY:
            fid = self.idempotency_keys.get((params["scope"], params["idempotency_key"]))
            return ([{"fact_id": fid}] if fid else []), None, None

        if query == WRITE_ENCRYPTED_FACT_QUERY:
            self.captured_write_params.append(dict(params))
            key = (params["scope"], params["idempotency_key"])
            if key in self.idempotency_keys:
                raise RuntimeError("ConstraintValidationFailed: duplicate IdempotencyKey")
            self.idempotency_keys[key] = params["fact_id"]
            edge = {
                "id": params["fact_id"],
                "scope": params["scope"],
                "status": params["status"],
                "version": params["version"],
                "valid_from": params["valid_from"],
                "valid_until": params["valid_until"],
                "confidence": params["confidence"],
                "trust_weight": params["trust_weight"],
                "subject_bidx": params["subject_bidx"],
                "predicate_bidx": params["predicate_bidx"],
                "object_bidx": params["object_bidx"],
                "search_tokens": list(params["search_tokens"]),
                "knowledge_ct": params["knowledge_ct"],
                "wrapped_dek": params["wrapped_dek"],
                "kid": params["kid"],
            }
            self.facts[params["fact_id"]] = edge
            return [{"id": params["fact_id"]}], None, None

        if query == GET_ENCRYPTED_FACT_QUERY:
            edge = self.facts.get(params["fact_id"])
            if edge and edge["scope"] == params["scope"]:
                return [{"props": dict(edge)}], None, None
            return [], None, None

        if query == FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY:
            rows = [
                {"props": dict(e)}
                for e in self.facts.values()
                if e["scope"] == params["scope"]
                and e["status"] == "active"
                and e["subject_bidx"] == params["subject_bidx"]
                and e["predicate_bidx"] == params["predicate_bidx"]
            ]
            return rows, None, None

        if query == ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY:
            old = self.facts.get(params["old_id"])
            if (
                not old
                or old["scope"] != params["scope"]
                or old["status"] != "active"
                or old["version"] != params["expected_version"]
            ):
                return [], None, None
            old["status"] = "superseded"
            old["valid_until"] = params["now"]
            old["knowledge_ct"] = params["closed_knowledge_ct"]
            old["wrapped_dek"] = params["closed_wrapped_dek"]
            old["kid"] = params["closed_kid"]
            new_edge = {
                "id": params["fact_id"],
                "scope": params["scope"],
                "status": "active",
                "version": params["version"],
                "valid_from": params["valid_from"],
                "valid_until": None,
                "confidence": params["confidence"],
                "trust_weight": params["trust_weight"],
                "subject_bidx": params["subject_bidx"],
                "predicate_bidx": params["predicate_bidx"],
                "object_bidx": params["object_bidx"],
                "search_tokens": list(params["search_tokens"]),
                "knowledge_ct": params["knowledge_ct"],
                "wrapped_dek": params["wrapped_dek"],
                "kid": params["kid"],
            }
            self.facts[params["fact_id"]] = new_edge
            return [{"id": params["fact_id"]}], None, None

        if query == FORGET_ENCRYPTED_FACT_QUERY:
            edge = self.facts.get(params["fact_id"])
            if not edge or edge["scope"] != params["scope"] or edge["status"] != "active":
                return [], None, None
            edge.update({
                "status": "revoked",
                "valid_until": params["now"],
                "knowledge_ct": None,
                "wrapped_dek": None,
                "kid": None,
                "subject_bidx": None,
                "predicate_bidx": None,
                "object_bidx": None,
                "search_tokens": [],
            })
            self.idempotency_keys = {k: v for k, v in self.idempotency_keys.items() if v != params["fact_id"]}
            return [{"id": params["fact_id"]}], None, None

        raise AssertionError(f"Unexpected query: {query[:60]}")


def _make_store(tmp_dir: str, field_hex_len: int = 24):
    key_path = Path(tmp_dir) / "keys.json"
    generate_key_file(key_path)
    provider = LocalKeyFileProvider(key_path)
    client = InMemoryEncryptedNeo4jMock()
    store = EncryptedNeo4jMemoryStore(client=client, key_provider=provider, field_hex_len=field_hex_len)
    return store, client, provider


def test_zero_plaintext_canary_in_stored_properties():
    with tempfile.TemporaryDirectory() as tmp:
        store, client, _ = _make_store(tmp)
        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])
        rec = MemoryRecord(
            id="fact-canary-1",
            subject="CANARY_ALICE_9941",
            predicate="LIVES_IN",
            object="CANARY_LONDON_8821",
            description="CANARY_ALICE_9941 lives in CANARY_LONDON_8821",
            scope="research",
            tenant_id="default",
            status=MemoryStatus.ACTIVE,
            version=1,
            confidence=0.95,
            trust_weight=0.9,
            valid_from=ctx.now,
        )
        store.write_fact(rec, ctx)

        serialized = str(client.captured_write_params) + str(client.entities) + str(client.facts)
        for canary in ("CANARY_ALICE_9941", "CANARY_LONDON_8821", "LIVES_IN"):
            assert canary not in serialized, f"Plaintext canary '{canary}' leaked into Neo4j properties!"

        loaded = store.get_fact("fact-canary-1", ctx, scope="research")
        assert loaded is not None
        assert loaded.subject == "CANARY_ALICE_9941"
        assert loaded.object == "CANARY_LONDON_8821"


def test_colliding_entities_and_facts_never_merge_or_cross_contaminate():
    with tempfile.TemporaryDirectory() as tmp:
        store, client, _ = _make_store(tmp, field_hex_len=2)
        bidx = store.blind_index
        # Find two distinct names that collide on 2-hex-char blind index
        seen: Dict[str, str] = {}
        name_a = name_b = ""
        for i in range(1000):
            cand = f"Entity_{i}"
            h = bidx.compute("entity", cand)
            if h in seen:
                name_a, name_b = seen[h], cand
                break
            seen[h] = cand
        assert name_a and name_b and bidx.compute("entity", name_a) == bidx.compute("entity", name_b)

        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])
        now_iso = ctx.now.isoformat()
        id_a = store.resolve_or_create_entity(name_a, "research", now_iso)
        id_b = store.resolve_or_create_entity(name_b, "research", now_iso)
        assert id_a != id_b, "Colliding entity names were wrongly merged!"
        assert store.resolve_or_create_entity(name_a, "research", now_iso) == id_a

        # Same collision check for facts
        seen_s: Dict[str, str] = {}
        subj_a = subj_b = ""
        for i in range(1000):
            cand = f"Subj_{i}"
            h = bidx.compute("subject", cand)
            if h in seen_s:
                subj_a, subj_b = seen_s[h], cand
                break
            seen_s[h] = cand
        assert subj_a and subj_b

        for fid, s, o in [("f-a", subj_a, "Paris"), ("f-b", subj_b, "Tokyo")]:
            store.write_fact(
                MemoryRecord(
                    id=fid, subject=s, predicate="LIVES_IN", object=o,
                    scope="research", tenant_id="default", valid_from=ctx.now,
                ),
                ctx,
            )
        hits_a = store.find_active_by_subject_predicate(subj_a, "LIVES_IN", ctx, scope="research")
        assert len(hits_a) == 1 and hits_a[0].id == "f-a" and hits_a[0].object == "Paris"


def test_section5_tamper_rejection_on_every_plaintext_field():
    with tempfile.TemporaryDirectory() as tmp:
        store, client, _ = _make_store(tmp)
        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])
        rec = MemoryRecord(
            id="fact-tamper", subject="Alice", predicate="LIVES_IN", object="Berlin",
            description="Alice lives in Berlin", scope="research", tenant_id="default",
            version=1, confidence=0.9, trust_weight=0.8, valid_from=ctx.now,
        )
        store.write_fact(rec, ctx)
        original = dict(client.facts["fact-tamper"])

        mutations = [
            {"status": "superseded"},
            {"version": 2},
            {"subject_bidx": "0" * 24},
            {"predicate_bidx": "0" * 24},
            {"object_bidx": "0" * 24},
            {"search_tokens": ["deadbeef12345678"]},
            {"trust_weight": 0.1},
            {"confidence": 0.1},
        ]
        for mut in mutations:
            client.facts["fact-tamper"] = {**original, **mut}
            try:
                store.get_fact("fact-tamper", ctx, scope="research")
                raise AssertionError(f"Tamper mutation was NOT rejected: {mut}")
            except (IntegrityMismatchError, DecryptionKeyError):
                pass


def test_key_loss_raises_error_and_key_rotation_preserves_readability():
    with tempfile.TemporaryDirectory() as tmp:
        store, client, provider = _make_store(tmp)
        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])
        old_kid = provider.bundle.current_kid

        store.write_fact(
            MemoryRecord(
                id="fact-rot-1", subject="Bob", predicate="WORKS_FOR", object="Acme",
                scope="research", tenant_id="default", valid_from=ctx.now,
            ),
            ctx,
        )
        # Rotate key: old fact must still decrypt under previous_kids, new fact seals under new kid
        new_bundle = provider.rotate_enc_key()
        assert new_bundle.current_kid != old_kid
        assert store.get_fact("fact-rot-1", ctx, scope="research").object == "Acme"

        # Rewrap DEK to new kid
        edge = client.facts["fact-rot-1"]
        rewrapped_kid, rewrapped_dek = store.cipher.rewrap_dek("research", "fact-rot-1", 1, edge["wrapped_dek"])
        assert rewrapped_kid == new_bundle.current_kid

        # Simulate key loss: replace wrapped_dek kid with unknown kid -> MUST raise DecryptionKeyError
        edge["wrapped_dek"] = edge["wrapped_dek"].replace(old_kid, "kid_missing00000", 1)
        edge["knowledge_ct"] = edge["knowledge_ct"].replace(old_kid, "kid_missing00000", 1)
        try:
            store.get_fact("fact-rot-1", ctx, scope="research")
            raise AssertionError("Missing kid did not raise DecryptionKeyError!")
        except DecryptionKeyError:
            assert store.decrypt_failure_count >= 1


def test_atomic_supersede_rewrites_closed_payload_and_rejects_stale_version():
    with tempfile.TemporaryDirectory() as tmp:
        store, client, _ = _make_store(tmp)
        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])
        old_rec = MemoryRecord(
            id="fact-v1", subject="Alice", predicate="LIVES_IN", object="London",
            scope="research", tenant_id="default", version=1, valid_from=ctx.now,
        )
        store.write_fact(old_rec, ctx)
        pre_supersede_ct = client.facts["fact-v1"]["knowledge_ct"]

        new_rec = MemoryRecord(
            id="fact-v2", subject="Alice", predicate="LIVES_IN", object="NYC",
            scope="research", tenant_id="default", version=2, valid_from=ctx.now,
        )
        store.atomic_supersede(old_rec, new_rec, ctx)

        # Closed edge payload was rewritten with status='superseded'
        closed = store.get_fact("fact-v1", ctx, scope="research")
        assert closed.status == MemoryStatus.SUPERSEDED
        assert client.facts["fact-v1"]["knowledge_ct"] != pre_supersede_ct

        # Stale concurrent supersede on old_rec (already superseded) MUST raise SupersessionConflictError
        try:
            store.atomic_supersede(old_rec, new_rec, ctx)
            raise AssertionError("Stale supersede did not raise SupersessionConflictError!")
        except SupersessionConflictError:
            pass

        # Forget clears ciphertext, bidx, and idempotency key
        assert store.forget_fact("fact-v2", ctx, scope="research") is True
        assert client.facts["fact-v2"]["knowledge_ct"] is None
        assert client.facts["fact-v2"]["subject_bidx"] is None
        assert client.facts["fact-v2"]["search_tokens"] == []


def test_stopword_filtering_and_deterministic_12_token_cap():
    bidx = BlindIndex(os.urandom(32))
    tokens = bidx.extract_search_tokens(
        subject="Alice",
        predicate="LIVES_IN",
        obj="London",
        description="The quick brown fox jumps over the lazy dog in a very extraordinary architectural metropolitan district downtown",
    )
    assert len(tokens) == 12
    assert bidx.compute_token("the") not in tokens
    assert bidx.compute_token("alice") in tokens
    assert bidx.compute_token("london") in tokens


if __name__ == "__main__":
    test_zero_plaintext_canary_in_stored_properties()
    test_colliding_entities_and_facts_never_merge_or_cross_contaminate()
    test_section5_tamper_rejection_on_every_plaintext_field()
    test_key_loss_raises_error_and_key_rotation_preserves_readability()
    test_atomic_supersede_rewrites_closed_payload_and_rejects_stale_version()
    test_stopword_filtering_and_deterministic_12_token_cap()
    print("ALL 6 PHASE 1 ENCRYPTION RELEASE-GATE TESTS PASSED!")
