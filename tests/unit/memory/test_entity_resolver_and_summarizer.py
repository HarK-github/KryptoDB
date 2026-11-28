"""Phase 4 Gate Tests: Block-then-LLM AliasArbiter & Event-Driven Hub EntitySummarizer."""

from __future__ import annotations

import json

from kryptodb.core.memory.entity_resolution import (
    AliasCandidateContext,
    AliasCluster,
    AliasClusterResult,
    EntityResolver,
)
from kryptodb.core.memory.entity_summarizer import EntityProfile, EntitySummarizer
from kryptodb.infrastructure.crypto.envelope import EnvelopeCipher
from kryptodb.infrastructure.crypto.keyring import LocalKeyFileProvider


def test_alias_arbiter_merges_confirmed_preserves_omitted_and_separates_conflicts() -> None:
    """T4.3 (1): Confirmed aliases merge; omitted candidates fall back to singletons; conflicting contexts stay separate."""
    resolver = EntityResolver()
    candidates = [
        AliasCandidateContext(name="Edwin Aldrin", entity_type="PERSON", summary="Apollo 11 lunar module pilot"),
        AliasCandidateContext(name="Buzz Aldrin", entity_type="PERSON", summary="Apollo 11 astronaut"),
        AliasCandidateContext(name="Michael Collins", entity_type="PERSON", summary="Apollo 11 command module pilot"),
        AliasCandidateContext(name="Neil Armstrong", entity_type="PERSON", summary="Armstrong — astronaut"),
        AliasCandidateContext(name="Louis Armstrong", entity_type="PERSON", summary="Armstrong — jazz trumpeter"),
    ]

    def mock_arbiter(entity_type, cands):
        # Confirms Edwin/Buzz merge, intentionally omits Michael Collins (to test singleton fallback),
        # and proposes a low-confidence merge of the two Armstrongs (must be blocked by safety invariant #2).
        return AliasClusterResult(
            clusters=[
                AliasCluster(
                    canonical_name="Buzz Aldrin",
                    aliases=["Edwin Aldrin", "Buzz Aldrin"],
                    entity_type="PERSON",
                    confidence=0.96,
                ),
                AliasCluster(
                    canonical_name="Neil Armstrong",
                    aliases=["Neil Armstrong", "Louis Armstrong"],
                    entity_type="PERSON",
                    confidence=0.65,  # below 0.90 threshold -> must split into singletons
                ),
            ]
        )

    res = resolver.arbitrate_aliases(candidates, entity_type="PERSON", arbiter=mock_arbiter)
    cluster_sets = [set(c.aliases) for c in res.clusters]

    assert {"Edwin Aldrin", "Buzz Aldrin"} in cluster_sets
    assert {"Michael Collins"} in cluster_sets  # omitted candidate preserved as singleton
    assert {"Neil Armstrong"} in cluster_sets
    assert {"Louis Armstrong"} in cluster_sets


def test_hub_summarizer_min_degree_and_encrypted_sealing(tmp_path) -> None:
    """T4.3 (2): Hub summarizer respects min_degree, updates on commit/supersede, and seals with AAD in encrypted mode."""
    facts = [
        {"id": "f1", "subject": "Alice", "predicate": "LIVES_IN", "object": "Paris", "status": "active", "valid_from": "2025-01-01"},
        {"id": "f2", "subject": "Alice", "predicate": "WORKS_AT", "object": "Acme", "status": "active", "valid_from": "2025-02-01"},
    ]
    plain_summarizer = EntitySummarizer(min_degree=3, storage_mode="plaintext")

    # Below min_degree=3 -> returns None
    assert plain_summarizer.on_fact_change("Alice", "default", facts, event_type="commit") is None

    # 3rd active fact triggers summary
    facts.append({"id": "f3", "subject": "Alice", "predicate": "KNOWS", "object": "Bob", "status": "active", "valid_from": "2025-03-01"})
    out_plain = plain_summarizer.on_fact_change("Alice", "default", facts, event_type="supersede")
    assert out_plain is not None
    assert out_plain["mode"] == "plaintext"
    profile: EntityProfile = out_plain["profile"]
    assert profile.entity_id == "Alice"
    assert profile.time_range_start == "2025-01-01"
    assert profile.time_range_end == "2025-03-01"
    assert set(profile.source_fact_ids) == {"f1", "f2", "f3"}

    # Encrypted mode seals with EnvelopeCipher + AAD = kdb:v1:{scope}:{entity_id}:profile
    import base64
    key_file = tmp_path / "kdb.key"
    key_file.write_text(
        json.dumps({
            "current_kid": "k1",
            "enc_key": base64.b64encode(b"\x01" * 32).decode("ascii"),
            "index_key": base64.b64encode(b"\x02" * 32).decode("ascii"),
        }),
        encoding="utf-8",
    )
    cipher = EnvelopeCipher(LocalKeyFileProvider(key_file=key_file))
    enc_summarizer = EntitySummarizer(min_degree=3, cipher=cipher, storage_mode="encrypted")

    out_enc = enc_summarizer.on_fact_change("Alice", "research", facts, event_type="commit")
    assert out_enc is not None
    assert out_enc["mode"] == "encrypted"
    assert out_enc["aad"] == "kdb:v1:research:Alice:profile"
    assert "profile" not in out_enc
    assert out_enc["sealed"]["profile_ct"].startswith("kdb:v1:k1:")
    assert cipher.open_entity_profile("research", "Alice", out_enc["sealed"])["entity_id"] == "Alice"
