"""Core Embedder protocol implementation.

LocalSentenceEmbedder is kept here as a HashEmbedder-compatible fallback
for unit tests. Production concrete ML adapters are injected from the
composition root.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, List, Optional

from kryptodb.core.embeddings.versioning import DEFAULT_EMBEDDING_METADATA, EmbeddingMetadata
from kryptodb.core.ports import Embedder

logger = logging.getLogger("kryptodb.core.embeddings.embedder")


class LocalSentenceEmbedder(Embedder):
    """Hash-based deterministic embedder for use in unit tests only.

    If a real model is injected via ``model=`` it is used; otherwise a
    fast SHA-256 deterministic fallback is used. Production code injects
    a concrete Embedder adapter at startup.
    """

    def __init__(
        self,
        metadata: Optional[EmbeddingMetadata] = None,
        model: Optional[Any] = None,
        use_real: Optional[bool] = None,
    ) -> None:
        self._meta = metadata or DEFAULT_EMBEDDING_METADATA
        self._model = model
        self._use_fallback = True  # always fallback; real models live in infrastructure/

        if self._model is not None:
            self._use_fallback = False

    @property
    def dimension(self) -> int:
        return self._meta.dimension

    @property
    def model_name(self) -> str:
        return self._meta.model_name

    @property
    def model_version(self) -> str:
        return self._meta.model_version

    def _fallback_vector(self, text: str) -> List[float]:
        cleaned = text.strip().lower()
        if not cleaned:
            return [0.0] * self.dimension
        base_hash = hashlib.sha256(cleaned.encode("utf-8")).digest()
        vec = []
        for i in range(self.dimension):
            byte_idx = (i * 4) % len(base_hash)
            val = (int.from_bytes(base_hash[byte_idx:byte_idx + 2], "little") / 65535.0) * 2.0 - 1.0
            vec.append(val)
        norm = sum(x * x for x in vec) ** 0.5
        return [x / norm for x in vec] if norm > 0 else vec

    def embed(self, text: str) -> List[float]:
        if not self._use_fallback and self._model is not None:
            try:
                emb = self._model.encode(text, normalize_embeddings=True)
                return emb.tolist() if hasattr(emb, "tolist") else list(emb)
            except Exception as exc:
                logger.warning("Real embed failed (%s); using fallback.", exc)
        return self._fallback_vector(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if not self._use_fallback and self._model is not None:
            try:
                embs = self._model.encode(texts, normalize_embeddings=True)
                return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embs]
            except Exception as exc:
                logger.warning("Real batch embed failed (%s); using fallback.", exc)
        return [self._fallback_vector(t) for t in texts]


# Default singleton instance
default_embedder = LocalSentenceEmbedder()


def get_embedder() -> LocalSentenceEmbedder:
    """Return the module-level default embedder (hash-based for unit tests).

    Production code should instantiate infrastructure.embeddings.sentence_transformer.LocalSentenceEmbedder.
    """
    return default_embedder
