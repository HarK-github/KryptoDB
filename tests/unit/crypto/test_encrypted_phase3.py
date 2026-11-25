"""Phase 3 Release-Gate Tests: Collision-Safe Forget, Idempotency Cleanup, & Allowlist Plaintext Audit."""

from __future__ import annotations

import ast
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord
from kryptodb.infrastructure.audit_ledger import AuditLedger
from kryptodb.infrastructure.crypto import LocalKeyFileProvider, RAMVectorIndex, generate_key_file
from kryptodb.infrastructure.neo4j.encrypted_queries import (
    AUDIT_ENTITY_PROPERTIES_QUERY,
    AUDIT_EPISODE_PROPERTIES_QUERY,
    AUDIT_EVENT_PROPERTIES_QUERY,
    AUDIT_FACT_PROPERTIES_QUERY,
    CREATE_ENCRYPTED_ENTITY_QUERY,
    FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY,
    FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY,
    FIND_ENTITIES_BY_BIDX_QUERY,
    FIND_IDEMPOTENCY_KEY_QUERY,
    FORGET_ENCRYPTED_FACT_QUERY,
    GET_ENCRYPTED_FACT_QUERY,
    SEARCH_ENCRYPTED_BY_TOKENS_QUERY,
    WRITE_ENCRYPTED_FACT_QUERY,
)
from kryptodb.infrastructure.neo4j.encrypted_retrieval import EncryptedRetrievalService
from kryptodb.infrastructure.neo4j.encrypted_store import EncryptedNeo4jMemoryStore
from kryptodb.workers.plaintext_audit import PlaintextAuditScanner


def _embed(text: str) -> List[float]:
    t = text.lower()
    v = [1.0 if "paris" in t else 0.1, 1.0 if "tokyo" in t else 0.1]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


class Phase3MockNeo4j:
    def __init__(self) -> None:
        self.driver = object()
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.facts: Dict[str, Dict[str, Any]] = {}
        self.endpoints: Dict[str, tuple[str, str]] = {}
        self.idempotency_keys: Dict[tuple[str, str], str] = {}

    def execute_query_with_retry(self, query: str, **params: Any):
        if query == FIND_ENTITIES_BY_BIDX_QUERY:
            return [
                e for e in self.entities.values()
                if e["scope"] == params["scope"] and e["name_bidx"] == params["name_bidx"]
            ], None, None

        if query == CREATE_ENCRYPTED_ENTITY_QUERY:
            rec = dict(params)
            rec["created_at"] = rec.pop("now")
            rec["updated_at"] = rec["created_at"]
            self.entities[params["id"]] = rec
            return [{"id": params["id"]}], None, None

        if query == FIND_IDEMPOTENCY_KEY_QUERY:
            fid = self.idempotency_keys.get((params["scope"], params["idempotency_key"]))
            return ([{"fact_id": fid}] if fid else []), None, None

        if query == WRITE_ENCRYPTED_FACT_QUERY:
            key = (params["scope"], params["idempotency_key"])
            if key in self.idempotency_keys:
                raise RuntimeError("Duplicate IdempotencyKey")
            self.idempotency_keys[key] = params["fact_id"]
            self.endpoints[params["fact_id"]] = (params["subject_entity_id"], params["object_entity_id"])
            self.facts[params["fact_id"]] = {
                "id": params["fact_id"],
                "scope": params["scope"],
                "status": params["status"],
                "version": params["version"],
                "valid_from": params["valid_from"],
                "valid_until": params["valid_until"],
                "recorded_from": params["now"],
                "recorded_to": None,
                "confidence": params["confidence"],
                "trust_weight": params["trust_weight"],
                "subject_bidx": params["subject_bidx"],
                "predicate_bidx": params["predicate_bidx"],
                "object_bidx": params["object_bidx"],
                "search_tokens": list(params["search_tokens"]),
                "knowledge_ct": params["knowledge_ct"],
                "wrapped_dek": params["wrapped_dek"],
                "kid": params["kid"],
                "created_at": params["now"],
            }
            return [{"id": params["fact_id"]}], None, None

        if query == GET_ENCRYPTED_FACT_QUERY:
            e = self.facts.get(params["fact_id"])
            return ([{"props": dict(e)}] if e and e["scope"] == params["scope"] else []), None, None

        if query == FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY:
            return [
                {"props": dict(e)}
                for e in self.facts.values()
                if e["scope"] == params["scope"]
                and e["status"] == "active"
                and e["subject_bidx"] == params["subject_bidx"]
                and e["predicate_bidx"] == params["predicate_bidx"]
            ], None, None

        if query == FORGET_ENCRYPTED_FACT_QUERY:
            e = self.facts.get(params["fact_id"])
            if not e or e["scope"] != params["scope"] or e["status"] != "active":
                return [], None, None
            e.update({
                "status": "revoked",
                "valid_until": params["now"],
                "recorded_to": params["now"],
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

        if query == FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY:
            s = set(params["fact_ids"])
            return [
                {"props": dict(e), "hops": 1}
                for fid, e in self.facts.items()
                if e["scope"] == params["scope"] and e["status"] == "active" and fid in s
            ], None, None

        if query == SEARCH_ENCRYPTED_BY_TOKENS_QUERY:
            tok_set = set(params["word_tokens"])
            ent_set = set(params["entity_bidxs"])
            rows = []
            for fid, e in self.facts.items():
                if e["scope"] != params["scope"] or e["status"] != "active":
                    continue
                s_id, o_id = self.endpoints[fid]
                if self.entities[s_id]["name_bidx"] in ent_set or self.entities[o_id]["name_bidx"] in ent_set or (tok_set & set(e["search_tokens"])):
                    rows.append({"props": dict(e), "hops": 1})
            return rows, None, None

        if query == AUDIT_ENTITY_PROPERTIES_QUERY:
            return [
                {"id": e["id"], "prop_keys": list(e.keys()), "has_ct": e.get("name_ct") is not None, "has_dek": e.get("wrapped_dek") is not None}
                for e in self.entities.values()
            ], None, None

        if query == AUDIT_FACT_PROPERTIES_QUERY:
            return [
                {"id": f["id"], "status": f.get("status"), "prop_keys": list(f.keys()), "has_ct": f.get("knowledge_ct") is not None, "has_dek": f.get("wrapped_dek") is not None}
                for f in self.facts.values()
            ], None, None

        if query in (AUDIT_EVENT_PROPERTIES_QUERY, AUDIT_EPISODE_PROPERTIES_QUERY):
            return [], None, None

        raise AssertionError(f"Unexpected query: {query[:60]}")


def test_collision_safe_forget_and_idempotency_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        key_path = Path(tmp) / "keys.json"
        generate_key_file(key_path)
        provider = LocalKeyFileProvider(key_path)
        client = Phase3MockNeo4j()
        ram_idx = RAMVectorIndex()
        ledger = AuditLedger(db_path=os.path.join(tmp, "audit.db"))
        store = EncryptedNeo4jMemoryStore(
            client=client,
            key_provider=provider,
            field_hex_len=2,
            vector_index=ram_idx,
            embed_fn=_embed,
            audit_ledger=ledger,
        )
        retrieval = EncryptedRetrievalService(store=store, vector_index=ram_idx, embed_fn=_embed)
        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])

        # Find two distinct subjects colliding on 2-hex subject_bidx
        seen: Dict[str, str] = {}
        subj_a = subj_b = ""
        for i in range(1000):
            cand = f"Person_{i}"
            h = store.blind_index.compute("subject", cand)
            if h in seen:
                subj_a, subj_b = seen[h], cand
                break
            seen[h] = cand
        assert subj_a and subj_b

        store.write_fact(
            MemoryRecord(id="f-a", subject=subj_a, predicate="LIVES_IN", object="Paris", description=f"{subj_a} lives in Paris", scope="research", tenant_id="default", valid_from=ctx.now),
            ctx,
        )
        store.write_fact(
            MemoryRecord(id="f-b", subject=subj_b, predicate="LIVES_IN", object="Tokyo", description=f"{subj_b} lives in Tokyo", scope="research", tenant_id="default", valid_from=ctx.now),
            ctx,
        )

        # Forget ONLY subj_a -> subj_b MUST remain untouched!
        forgotten = store.forget_by_subject_predicate(subj_a, "LIVES_IN", ctx, scope="research")
        assert forgotten == ["f-a"]
        assert client.facts["f-a"]["status"] == "revoked"
        assert client.facts["f-a"]["knowledge_ct"] is None
        assert client.facts["f-a"]["subject_bidx"] is None
        assert client.facts["f-a"]["search_tokens"] == []
        assert len(client.idempotency_keys) == 1  # f-a's idempotency key deleted, f-b's kept
        assert ram_idx.count("research") == 1

        # Negative retrieval proof for f-a (both Path A and Path B)
        assert store.find_active_by_subject_predicate(subj_a, "LIVES_IN", ctx, scope="research") == []
        assert all(r.fact_id != "f-a" for r in retrieval.search("Paris", ctx, scope="research"))

        # subj_b is still active & retrievable
        b_hits = store.find_active_by_subject_predicate(subj_b, "LIVES_IN", ctx, scope="research")
        assert len(b_hits) == 1 and b_hits[0].id == "f-b"

        # Audit ledger recorded the forget without plaintext
        assert ledger.verify_chain() is True
        entries = ledger.list_recent()
        assert len(entries) == 1 and entries[0]["action"] == "forget_encrypted_fact"
        assert "Paris" not in str(entries) and subj_a not in str(entries)


def test_allowlist_plaintext_audit_passes_clean_and_flags_violations():
    with tempfile.TemporaryDirectory() as tmp:
        key_path = Path(tmp) / "keys.json"
        generate_key_file(key_path)
        provider = LocalKeyFileProvider(key_path)
        client = Phase3MockNeo4j()
        store = EncryptedNeo4jMemoryStore(client=client, key_provider=provider)
        ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])

        store.write_fact(
            MemoryRecord(id="f-1", subject="Alice", predicate="LIVES_IN", object="London", scope="research", tenant_id="default", valid_from=ctx.now),
            ctx,
        )
        store.write_fact(
            MemoryRecord(id="f-2", subject="Bob", predicate="LIVES_IN", object="Rome", scope="research", tenant_id="default", valid_from=ctx.now),
            ctx,
        )
        store.forget_fact("f-2", ctx, scope="research")

        scanner = PlaintextAuditScanner(client=client)
        clean_report = scanner.scan()
        assert clean_report.clean is True, f"Unexpected violations on clean graph: {clean_report.violations}"

        # Inject a disallowed plaintext property ('description') and a missing ciphertext on an active fact
        client.facts["f-1"]["description"] = "LEAKED_PLAINTEXT"
        client.facts["f-1"]["knowledge_ct"] = None
        dirty_report = scanner.scan()
        assert dirty_report.clean is False
        assert dirty_report.violation_count == 2
        assert scanner.total_violations_metric == 2


def test_plaintext_audit_worker_has_zero_key_or_crypto_imports():
    repo_root = Path(__file__).resolve().parents[3]
    audit_file = repo_root / "kryptodb/workers/plaintext_audit.py"
    tree = ast.parse(audit_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "crypto" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "crypto" not in mod and "cryptography" not in mod
    assert "MATCH (" not in audit_file.read_text(encoding="utf-8")


if __name__ == "__main__":
    test_collision_safe_forget_and_idempotency_cleanup()
    test_allowlist_plaintext_audit_passes_clean_and_flags_violations()
    test_plaintext_audit_worker_has_zero_key_or_crypto_imports()
    print("ALL 3 PHASE 3 ERASURE & PLAINTEXT-AUDIT GATE TESTS PASSED!")
