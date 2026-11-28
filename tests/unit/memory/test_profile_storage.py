from __future__ import annotations

import base64
import json

import pytest

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.entity_summarizer import EntitySummarizer
from kryptodb.core.memory.memory_service import MemoryService
from kryptodb.infrastructure.crypto.envelope import EnvelopeCipher, DecryptionKeyError
from kryptodb.infrastructure.crypto.keyring import LocalKeyFileProvider


PROFILE_FACTS = [
    {"id": f"fact-{i}", "subject": "Hub", "predicate": "RELATED_TO", "object": f"Entity {i}",
     "status": "active", "valid_from": f"2025-01-0{i}"}
    for i in range(1, 4)
]


class ProfileStore:
    def __init__(self) -> None:
        self.saved = []

    def list_incident_facts(self, subject, context, limit=25):
        return PROFILE_FACTS[:limit]

    def save_entity_profile(self, entity_id, scope, profile_payload, context):
        self.saved.append((entity_id, scope, profile_payload, context))


def _cipher(tmp_path):
    key_file = tmp_path / "profile-keys.json"
    key_file.write_text(json.dumps({
        "current_kid": "k1",
        "enc_key": base64.b64encode(b"\x01" * 32).decode("ascii"),
        "index_key": base64.b64encode(b"\x02" * 32).decode("ascii"),
    }), encoding="utf-8")
    return EnvelopeCipher(LocalKeyFileProvider(key_file=key_file))


def test_entity_profile_envelope_uses_scope_and_entity_bound_aad(tmp_path):
    cipher = _cipher(tmp_path)
    profile = {"entity_id": "entity-1", "summary": "Hub summary", "key_facts": ["fact"]}

    sealed = cipher.seal_entity_profile("research", "entity-1", profile)

    assert set(sealed) == {"profile_ct", "profile_wrapped_dek", "profile_kid"}
    assert cipher.open_entity_profile("research", "entity-1", sealed) == profile
    with pytest.raises(DecryptionKeyError):
        cipher.open_entity_profile("research", "different-entity", sealed)


def test_memory_service_persists_summarizer_profile_from_incident_facts():
    store = ProfileStore()
    service = MemoryService(store=store, entity_summarizer=EntitySummarizer(min_degree=3))
    context = RequestContext.create(tenant_id="default", agent_id="test", active_scopes=["research"])

    service._maybe_update_hub_summary("Hub", "research", context, "commit")

    assert len(store.saved) == 1
    entity_id, scope, payload, saved_context = store.saved[0]
    assert entity_id == "Hub"
    assert scope == "research"
    assert payload["mode"] == "plaintext"
    assert payload["profile"].source_fact_ids == ["fact-1", "fact-2", "fact-3"]
    assert saved_context is context
