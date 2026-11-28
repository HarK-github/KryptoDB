from __future__ import annotations

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.entity_resolution import AliasCluster, AliasClusterResult, EntityResolver
from kryptodb.core.memory.memory_service import MemoryService
from tests.fakes import FakeMemoryStore


def test_proposals_resolve_arbiter_approved_aliases_to_canonical_entity():
    def arbiter(entity_type, candidates):
        return AliasClusterResult(clusters=[
            AliasCluster(
                canonical_name="Buzz Aldrin",
                aliases=[candidate.name for candidate in candidates],
                entity_type="PERSON",
                confidence=0.99,
            )
        ])

    store = FakeMemoryStore()
    resolver = EntityResolver(arbiter=arbiter)
    service = MemoryService(store=store, entity_resolver=resolver)
    context = RequestContext.create(tenant_id="tenant", agent_id="test", active_scopes=["default"])

    service.propose_memory("Buzz Aldrin", "FLEW_ON", "Apollo 11", context)
    second = service.propose_memory("Edwin Aldrin", "FLEW_ON", "Apollo 11", context)

    assert second.subject == "Buzz Aldrin"
    assert len({fact.subject.casefold() for fact in store.facts.values()}) == 1
