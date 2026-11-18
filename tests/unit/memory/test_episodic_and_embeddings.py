"""Unit tests for Phase 4: Episodic layer, Embeddings versioning, and Failure resilience."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from kryptodb.core.context import RequestContext
from kryptodb.core.embeddings.embedder import LocalSentenceEmbedder
from kryptodb.core.embeddings.versioning import (
    EmbeddingMetadata,
    validate_embedding_compatibility,
)
from kryptodb.core.embeddings.queue import embed_with_resilience
from kryptodb.core.episodic.events import record_event
from kryptodb.core.episodic.summaries import summarize_session
from tests.fakes import FakeEventStore


def test_embedding_versioning_and_validation():
    meta1 = EmbeddingMetadata(model_name="all-MiniLM-L6-v2", model_version="1.0.0", dimension=384)
    meta2 = EmbeddingMetadata(model_name="all-MiniLM-L6-v2", model_version="1.0.0", dimension=384)
    assert validate_embedding_compatibility(meta1, meta2) is True

    # Dimension mismatch
    meta_diff_dim = EmbeddingMetadata(model_name="all-MiniLM-L6-v2", dimension=768)
    with pytest.raises(ValueError, match="dimension mismatch"):
        validate_embedding_compatibility(meta1, meta_diff_dim)

    # Model mismatch
    meta_diff_model = EmbeddingMetadata(model_name="text-embedding-3-small", dimension=384)
    with pytest.raises(ValueError, match="model mismatch"):
        validate_embedding_compatibility(meta1, meta_diff_model)


def test_embed_with_resilience():
    embedder = LocalSentenceEmbedder(use_real=False)

    # Normal success
    vec, pending = embed_with_resilience(embedder, "Hello World")
    assert pending is False
    assert vec is not None
    assert len(vec) == 384

    # Empty text -> (None, False)
    vec_empty, pending_empty = embed_with_resilience(embedder, "")
    assert pending_empty is False
    assert vec_empty is None

    # Broken embedder -> (None, True)
    class BrokenEmbedder:
        def embed(self, text: str):
            raise ConnectionError("Service unreachable")

    vec_fail, pending_fail = embed_with_resilience(BrokenEmbedder(), "Test text")
    assert pending_fail is True
    assert vec_fail is None


def test_episodic_events_and_summaries():
    event_store = FakeEventStore()
    ctx = RequestContext.create(tenant_id="tenant-acme", agent_id="agent-007")

    # 1. Record events
    ev1 = record_event(
        session_id="sess-abc",
        role="user",
        content="I prefer vegetarian meals and live on Oak Street.",
        context=ctx,
        store=event_store,
    )
    ev2 = record_event(
        session_id="sess-abc",
        role="assistant",
        content="Saved! I have recorded your vegetarian preference.",
        context=ctx,
        store=event_store,
    )

    assert ev1.id.startswith("ev-")
    assert ev2.id.startswith("ev-")

    # 2. List events
    history = event_store.list_by_session("sess-abc", ctx)
    assert len(history) == 2

    # 3. Summarize session
    summary = summarize_session(
        session_id="sess-abc",
        context=ctx,
        store=event_store,
    )

    assert summary.session_id == "sess-abc"
    assert "vegetarian" in summary.summary.lower()
    assert summary.topics is not None
