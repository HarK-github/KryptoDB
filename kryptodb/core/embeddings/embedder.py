"""Core Embedder protocol implementation."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import List, Optional

from kryptodb.core.embeddings.versioning import DEFAULT_EMBEDDING_METADATA, EmbeddingMetadata
from kryptodb.core.ports import Embedder

logger = logging.getLogger("kryptodb.core.embeddings.embedder")


class LocalSentenceEmbedder(Embedder):
    """Local, CPU-friendly dense vector embedder implementing Embedder protocol."""

    def __init__(
        self,
        metadata: Optional[EmbeddingMetadata] = None,
        use_real: Optional[bool] = None,
    ) -> None:
        self._meta = metadata or DEFAULT_EMBEDDING_METADATA
        self._model = None
        self._use_fallback = False

        should_use_real = (
            use_real
            if use_real is not None
            else (os.getenv("KRYPTODB_USE_REAL_EMBEDDINGS", "0") == "1")
        )

        if should_use_real:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._meta.model_name)
                logger.info("SentenceTransformer loaded: %s", self._meta.model_name)
            except Exception as exc:
                logger.warning("Could not load real SentenceTransformer (%s): %s. Using deterministic fallback.", self._meta.model_name, exc)
                self._use_fallback = True
        else:
            self._use_fallback = True

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
