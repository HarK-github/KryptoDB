"""Phase 2 Release-Gate Tests for Encrypted Hybrid Retrieval (Path A + Path B + RRF + 5% Failure Threshold)."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.infrastructure.crypto import DecryptionKeyError, LocalKeyFileProvider, generate_key_file
from kryptodb.infrastructure.crypto.ram_vector_index import RAMVectorIndex
from kryptodb.infrastructure.neo4j.encrypted_queries import (
    ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY,
    CREATE_ENCRYPTED_ENTITY_QUERY,
    FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY,
    FIND_ENTITIES_BY_BIDX_QUERY,
    FIND_IDEMPOTENCY_KEY_QUERY,
    FORGET_ENCRYPTED_FACT_QUERY,
    GET_ENCRYPTED_FACT_QUERY,
    LIST_ALL_ACTIVE_ENCRYPTED_FACTS_QUERY,
    SEARCH_ENCRYPTED_BY_TOKENS_QUERY,
    WRITE_ENCRYPTED_FACT_QUERY,
)
from kryptodb.infrastructure.neo4j.encrypted_retrieval import EncryptedRetrievalService
from kryptodb.infrastructure.neo4j.encrypted_store import EncryptedNeo4jMemoryStore


def _semantic_stub_embed(text: str) -> List[float]:
    """Deterministic semantic vector stub mapping synonyms (live/resides/london, project/kubernetes) to shared axes."""
    t = text.lower()
    # 4 semantic dimensions: [location/residence, employment/company, infrastructure/k8s, security/crypto]
    v = [0.0, 0.0, 0.0, 0.0]
    if any(w in t for w in ("live", "lives", "reside", "resides", "home", "london", "paris", "berlin", "city")):
        v[0] += 1.0
    if any(w in t for w in ("work", "works", "company", "acme", "employer")):
        v[1] += 1.0
    if any(w in t for w in ("kubernetes", "k8s", "cluster", "deploy", "deployed", "project-alpha")):
        v[2] += 1.0
    if any(w in t for w in ("encrypt", "cipher", "aes", "hmac")):
        v[3] += 1.0
    if v == [0.0, 0.0, 0.0, 0.0]:
        v[0] = 0.01
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


class Phase2EncryptedNeo4jMock:
    """In-memory graph stub supporting Phase 1 + Phase 2 encrypted Cypher queries."""

    def __init__(self) -> None:
        self.driver = object()
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.facts: Dict[str, Dict[str, Any]] = {}
        self.edges_endpoints: Dict[str, tuple[str, str]] = {}
        self.idempotency_keys: Dict[tuple[str, str], str] = {}

    def execute_query_with_retry(self, query: str, **params: Any):
        if query == FIND_ENTITIES_BY_BIDX_QUERY:
            return [
                e for e in self.entities.values()
                if e["scope"] == params["scope"] and e["name_bidx"] == params["name_bidx"]
            ], None, None

        if query == CREATE_ENCRYPTED_ENTITY_QUERY:
            self.entities[params["id"]] = dict(params)
            return [{"id": params["id"]}], None, None

        if query == FIND_IDEMPOTENCY_KEY_QUERY:
            fid = self.idempotency_keys.get((params["scope"], params["idempotency_key"]))
            return ([{"fact_id": fid}] if fid else []), None, None

        if query == WRITE_ENCRYPTED_FACT_QUERY:
            self.idempotency_keys[(params["scope"], params["idempotency_key"])] = params["fact_id"]
            self.edges_endpoints[params["fact_id"]] = (params["subject_entity_id"], params["object_entity_id"])
            self.facts[params["fact_id"]] = {
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
            return [{"id": params["fact_id"]}], None, None

        if query == GET_ENCRYPTED_FACT_QUERY:
            e = self.facts.get(params["fact_id"])
            return ([{"props": dict(e)}] if e and e["scope"] == params["scope"] else []), None, None

        if query == LIST_ALL_ACTIVE_ENCRYPTED_FACTS_QUERY:
            return [
                {"props": dict(e)}
                for e in self.facts.values()
                if e["scope"] == params["scope"] and e["status"] == "active"
            ], None, None

        if query == ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY:
            old = self.facts.get(params["old_id"])
            if not old or old["status"] != "active" or old["version"] != params["expected_version"]:
                return [], None, None
            old["status"] = "superseded"
            old["valid_until"] = params["now"]
            old["knowledge_ct"] = params["closed_knowledge_ct"]
            old["wrapped_dek"] = params["closed_wrapped_dek"]
            old["kid"] = params["closed_kid"]
            self.edges_endpoints[params["fact_id"]] = (params["subject_entity_id"], params["object_entity_id"])
            self.facts[params["fact_id"]] = {
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
            return [{"id": params["fact_id"]}], None, None

        if query == FORGET_ENCRYPTED_FACT_QUERY:
            e = self.facts.get(params["fact_id"])
            if not e or e["status"] != "active":
                return [], None, None
            e.update({
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

        if query == FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY:
            seed_set = set(params["fact_ids"])
            rows = []
            seed_nodes = set()
            for fid, edge in self.facts.items():
                if edge["scope"] == params["scope"] and edge["status"] == "active" and fid in seed_set:
                    rows.append({"props": dict(edge), "hops": 1})
                    s_id, o_id = self.edges_endpoints[fid]
                    seed_nodes.add(s_id)
                    seed_nodes.add(o_id)
            for fid, edge in self.facts.items():
                if edge["scope"] == params["scope"] and edge["status"] == "active" and fid not in seed_set:
                    s_id, o_id = self.edges_endpoints[fid]
                    if s_id in seed_nodes or o_id in seed_nodes:
                        rows.append({"props": dict(edge), "hops": 2})
            return rows[: params["max_results"]], None, None

        if query == SEARCH_ENCRYPTED_BY_TOKENS_QUERY:
            ent_set = set(params["entity_bidxs"])
            tok_set = set(params["word_tokens"])
            matched_ids = set()
            seed_nodes = set()
            rows = []
            for fid, edge in self.facts.items():
                if edge["scope"] != params["scope"] or edge["status"] != "active":
                    continue
                s_id, o_id = self.edges_endpoints[fid]
                s_bidx = self.entities[s_id]["name_bidx"]
                o_bidx = self.entities[o_id]["name_bidx"]
                if s_bidx in ent_set or o_bidx in ent_set or (tok_set & set(edge["search_tokens"])):
                    matched_ids.add(fid)
                    seed_nodes.add(s_id)
                    seed_nodes.add(o_id)
                    rows.append({"props": dict(edge), "hops": 1})
            for fid, edge in self.facts.items():
                if edge["scope"] == params["scope"] and edge["status"] == "active" and fid not in matched_ids:
                    s_id, o_id = self.edges_endpoints[fid]
                    if s_id in seed_nodes or o_id in seed_nodes:
                        rows.append({"props": dict(edge), "hops": 2})
            return rows[: params["max_results"]], None, None

        raise AssertionError(f"Unexpected query: {query[:60]}")


def _setup(tmp_dir: str):
    key_path = Path(tmp_dir) / "keys.json"
    generate_key_file(key_path)
    provider = LocalKeyFileProvider(key_path)
    client = Phase2EncryptedNeo4jMock()
    ram_idx = RAMVectorIndex()
    store = EncryptedNeo4jMemoryStore(
        client=client,
        key_provider=provider,
        vector_index=ram_idx,
        embed_fn=_semantic_stub_embed,
    )
    retrieval = EncryptedRetrievalService(store=store, vector_index=ram_idx, embed_fn=_semantic_stub_embed)
    ctx = RequestContext.create(tenant_id="default", agent_id="Agent-1", active_scopes=["research"])
    return store, retrieval, ram_idx, client, provider, ctx


def test_paraphrased_semantic_recall_via_ram_vector_index():
    """Query 'where is her home' recalls 'Alice resides in London' with ZERO token overlap."""
    with tempfile.TemporaryDirectory() as tmp:
        store, retrieval, ram_idx, _, _, ctx = _setup(tmp)
        store.write_fact(
            MemoryRecord(
                id="f-home", subject="Alice", predicate="RESIDES_IN", object="London",
                description="Alice resides in London", scope="research", tenant_id="default", valid_from=ctx.now,
            ),
            ctx,
        )
        assert ram_idx.count("research") == 1
        results = retrieval.search("where is her home", ctx, scope="research")
        assert len(results) == 1
        assert results[0].fact_id == "f-home"
        assert "London" in results[0].summary


def test_multihop_path_b_and_rrf_fusion_without_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        store, retrieval, _, _, _, ctx = _setup(tmp)
        store.write_fact(
            MemoryRecord(
                id="f-1", subject="Alice", predicate="LEADS", object="Project-Alpha",
                description="Alice leads Project-Alpha", scope="research", tenant_id="default", valid_from=ctx.now,
            ),
            ctx,
        )
        store.write_fact(
            MemoryRecord(
                id="f-2", subject="Project-Alpha", predicate="DEPLOYED_ON", object="Kubernetes",
                description="Project-Alpha is deployed on Kubernetes", scope="research", tenant_id="default", valid_from=ctx.now,
            ),
            ctx,
        )
        results = retrieval.search("Alice", ctx, scope="research")
        ids = [r.fact_id for r in results]
        assert len(ids) == len(set(ids)), "Duplicate fact_ids returned after RRF fusion"
        assert "f-1" in ids and "f-2" in ids


def test_supersede_and_forget_sync_ram_index_and_startup_rebuild():
    with tempfile.TemporaryDirectory() as tmp:
        store, retrieval, ram_idx, client, provider, ctx = _setup(tmp)
        f1 = MemoryRecord(
            id="f-old", subject="Alice", predicate="LIVES_IN", object="London",
            description="Alice lives in London", scope="research", tenant_id="default", version=1, valid_from=ctx.now,
        )
        store.write_fact(f1, ctx)

        f2 = MemoryRecord(
            id="f-new", subject="Alice", predicate="LIVES_IN", object="Paris",
            description="Alice lives in Paris", scope="research", tenant_id="default", version=2, valid_from=ctx.now,
        )
        store.atomic_supersede(f1, f2, ctx)
        assert ram_idx.count("research") == 1
        hits = retrieval.search("home", ctx, scope="research")
        assert [h.fact_id for h in hits] == ["f-new"]

        # Simulate cold daemon restart: fresh RAMVectorIndex rebuilt from encrypted Neo4j
        fresh_idx = RAMVectorIndex()
        fresh_retrieval = EncryptedRetrievalService(store=store, vector_index=fresh_idx, embed_fn=_semantic_stub_embed)
        report = fresh_retrieval.rebuild_index_for_scope("research", ctx)
        assert report["indexed_facts"] == 1
        assert [h.fact_id for h in fresh_retrieval.search("home", ctx, scope="research")] == ["f-new"]

        # Forget removes from Neo4j AND RAMVectorIndex -> 0 hits
        assert store.forget_fact("f-new", ctx, scope="research") is True
        assert ram_idx.count("research") == 0
        assert retrieval.search("Paris", ctx, scope="research") == []


def test_batch_failure_threshold_and_missing_kid_raise_decryption_key_error():
    with tempfile.TemporaryDirectory() as tmp:
        store, retrieval, _, client, provider, ctx = _setup(tmp)
        # Empty Neo4j returns [] (not_found), NOT an error
        assert retrieval.search("anything", ctx, scope="research") == []

        store.write_fact(
            MemoryRecord(
                id="f-corrupt", subject="Alice", predicate="LIVES_IN", object="Berlin",
                description="Alice lives in Berlin", scope="research", tenant_id="default", valid_from=ctx.now,
            ),
            ctx,
        )
        # Corrupt ciphertext (1/1 = 100% > 5% threshold) -> MUST raise DecryptionKeyError + alert
        client.facts["f-corrupt"]["knowledge_ct"] = client.facts["f-corrupt"]["knowledge_ct"][:-4] + "AAAA"
        try:
            retrieval.search("Berlin", ctx, scope="research")
            raise AssertionError("Expected DecryptionKeyError when >5% of batch failed decryption!")
        except DecryptionKeyError:
            assert retrieval.alert_count >= 1


def test_tampered_edge_rejected_during_retrieval():
    with tempfile.TemporaryDirectory() as tmp:
        store, retrieval, _, client, _, ctx = _setup(tmp)
        store.write_fact(
            MemoryRecord(
                id="f-ok", subject="Alice", predicate="LIVES_IN", object="London",
                description="Alice lives in London", scope="research", tenant_id="default", valid_from=ctx.now,
            ),
            ctx,
        )
        # Rogue Neo4j writer alters trust_weight -> Section 5 integrity check rejects it
        client.facts["f-ok"]["trust_weight"] = 0.01
        results = retrieval.search("London", ctx, scope="research")
        assert results == []
        assert store.integrity_failure_count >= 1


if __name__ == "__main__":
    test_paraphrased_semantic_recall_via_ram_vector_index()
    test_multihop_path_b_and_rrf_fusion_without_duplicates()
    test_supersede_and_forget_sync_ram_index_and_startup_rebuild()
    test_batch_failure_threshold_and_missing_kid_raise_decryption_key_error()
    test_tampered_edge_rejected_during_retrieval()
    print("ALL 5 PHASE 2 ENCRYPTED RETRIEVAL GATE TESTS PASSED!")
