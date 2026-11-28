"""Fix 3 Gate Tests: Verify EncryptedRetrievalService honors max_hops and relevance_cutoff."""

from __future__ import annotations

from unittest.mock import MagicMock

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord, MemoryStatus
from kryptodb.infrastructure.neo4j.encrypted_retrieval import EncryptedRetrievalService


def _make_record(fid: str, subj: str, pred: str, obj: str) -> MemoryRecord:
    return MemoryRecord(
        id=fid,
        subject=subj,
        predicate=pred,
        object=obj,
        description=f"{subj} {pred} {obj}",
        scope="research",
        tenant_id="default",
        status=MemoryStatus.ACTIVE,
        confidence=1.0,
    )


def test_encrypted_retrieval_honors_max_hops_and_relevance_cutoff() -> None:
    """Fix 3: max_hops=0 skips 2-hop neighbors and relevance_cutoff filters lower-scoring hits."""
    store = MagicMock()
    store.blind_index.compute.return_value = "bidx"
    store.blind_index.compute_token.return_value = "tok"

    # Mock Neo4j rows returning 1 direct seed fact (hops=1) and 1 2-hop neighbor (hops=2)
    store._run_query.side_effect = [
        [
            {"props": {"id": "f-direct"}, "hops": 1},
            {"props": {"id": "f-2hop"}, "hops": 2},
        ],
        [],
        # Second call (with max_hops=2 and high relevance_cutoff=0.9)
        [
            {"props": {"id": "f-direct"}, "hops": 1},
            {"props": {"id": "f-2hop"}, "hops": 2},
        ],
        [],
    ]
    records = {
        "f-direct": _make_record("f-direct", "Alice", "LIVES_IN", "Paris"),
        "f-2hop": _make_record("f-2hop", "Paris", "LOCATED_IN", "France"),
    }
    store._decrypt_edge.side_effect = lambda props: records[props["id"]]

    vec_index = MagicMock()
    vec_index.search.return_value = [("f-direct", 0.95)]

    svc = EncryptedRetrievalService(store=store, vector_index=vec_index, embed_fn=lambda q: [0.1] * 8)
    ctx = RequestContext.create(tenant_id="default", agent_id="a1", active_scopes=["research"])

    # 1. max_hops=0 must exclude 2-hop neighbors
    hits_0hop = svc.search("Alice", ctx, top_k=10, max_hops=0)
    assert [h.fact_id for h in hits_0hop] == ["f-direct"]

    # 2. relevance_cutoff=0.9 filters out lower-scoring 2-hop hit while keeping f-direct (final_score=1.0)
    hits_cutoff_09 = svc.search("Alice", ctx, top_k=10, max_hops=2, relevance_cutoff=0.9)
    assert [h.fact_id for h in hits_cutoff_09] == ["f-direct"]
