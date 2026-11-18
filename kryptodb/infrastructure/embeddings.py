from __future__ import annotations

import os
import hashlib
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("kryptodb.embeddings")

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_DIMENSIONS = 384
DEFAULT_VERSION = 1

class EmbeddingService:
    """
    Production-grade embedding service with model versioning, fast batching,
    and graceful fallback for offline testing/write-resilience.
    """
    _instance: Optional[EmbeddingService] = None

    def __new__(cls, *args, **kwargs) -> EmbeddingService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        version: int = DEFAULT_VERSION
    ) -> None:
        if getattr(self, "_initialized", False):
            return
            
        self.model_name = os.getenv("KRYPTODB_EMBEDDING_MODEL", model_name)
        self.dimensions = int(os.getenv("KRYPTODB_EMBEDDING_DIMS", str(dimensions)))
        self.version = version
        self._model = None
        self._use_fallback = False
        
        # Check if real embeddings are disabled via environment
        if os.getenv("KRYPTODB_USE_REAL_EMBEDDINGS", "0") == "1":
            self._load_model()
        else:
            self._use_fallback = True
            
        self._initialized = True

    def _load_model(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._use_fallback = False
            logger.info("Loaded real embedding model: %s", self.model_name)
        except Exception as exc:
            logger.warning("Failed to load SentenceTransformer (%s). Using deterministic fallback: %s", self.model_name, exc)
            self._use_fallback = True

    def _fallback_embedding(self, text: str) -> List[float]:
        """
        Deterministic, normalized pseudo-embedding derived from sha256 hash.
        Guarantees reproducible float vectors for unit tests and offline environments.
        """
        if not text:
            return [0.0] * self.dimensions
            
        vector = []
        # Seed chunks of sha256 to generate pseudo-dimensions
        base_hash = hashlib.sha256(text.encode("utf-8")).digest()
        for i in range(self.dimensions):
            byte_idx = (i * 4) % len(base_hash)
            val = (int.from_bytes(base_hash[byte_idx:byte_idx+2], "little") / 65535.0) * 2.0 - 1.0
            vector.append(val)
            
        # L2-normalize
        norm = sum(x * x for x in vector) ** 0.5
        if norm > 0:
            vector = [x / norm for x in vector]
        return vector

    def embed_query(self, text: str) -> List[float]:
        """Encodes query text to vector."""
        if not self._use_fallback and self._model is not None:
            try:
                emb = self._model.encode(text, normalize_embeddings=True)
                return emb.tolist() if hasattr(emb, "tolist") else list(emb)
            except Exception as exc:
                logger.error("Error computing real embedding: %s. Using fallback.", exc)
        return self._fallback_embedding(text)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Batch encodes multiple documents/sentences."""
        if not texts:
            return []
        if not self._use_fallback and self._model is not None:
            try:
                embs = self._model.encode(texts, normalize_embeddings=True)
                return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embs]
            except Exception as exc:
                logger.error("Error in batch embedding: %s. Using fallback.", exc)
        return [self._fallback_embedding(t) for t in texts]

    def embed_with_resilience(self, text: str) -> Tuple[List[float], bool]:
        """
        Returns (embedding, is_pending).
        If embedding fails completely, returns empty list with is_pending=True
        so writes are never blocked.
        """
        try:
            vec = self.embed_query(text)
            return vec, False
        except Exception as exc:
            logger.error("Embedding completely failed for '%s': %s", text, exc)
            return [], True

embedding_service = EmbeddingService()
