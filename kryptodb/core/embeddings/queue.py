"""Embedding failure resilience and asynchronous queueing helper."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("kryptodb.core.embeddings.queue")


def embed_with_resilience(
    embedder: Any,
    text: str,
) -> Tuple[Optional[List[float]], bool]:
    """Attempts dense vector generation with graceful failure containment.
    
    Returns (vector, False) on success.
    On failure or timeout, returns (None, True) with pending flag marked,
    ensuring write paths remain non-blocking.
    """
    if not text or not text.strip():
        return None, False

    try:
        if hasattr(embedder, "embed"):
            vec = embedder.embed(text)
            return vec, False
        elif hasattr(embedder, "embed_query"):
            vec = embedder.embed_query(text)
            return vec, False
        return None, True
    except Exception as exc:
        logger.warning("Embedding computation failed; marking pending for worker: %s", exc)
        return None, True
