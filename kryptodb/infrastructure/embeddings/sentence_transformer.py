"""Infrastructure-layer concrete sentence-transformer embedder and cross-encoder reranker.

These classes live outside core/ to keep the hexagonal boundary clean:
  core/ports.Embedder  <-- LocalSentenceEmbedder
  core/ports.Reranker  <-- CrossEncoderReranker

They are instantiated once in kryptodb/daemon/app.py (the composition root)
and injected into every service that needs them.
"""

from __future__ import annotations

import logging
import math
import re
from typing import List, Tuple

from kryptodb.core.embeddings.versioning import DEFAULT_EMBEDDING_METADATA, EmbeddingMetadata
from kryptodb.core.ports import CandidateItem, Embedder, Reranker

logger = logging.getLogger("kryptodb.infrastructure.embeddings.sentence_transformer")


class LocalSentenceEmbedder:
    """Concrete Embedder wrapping HuggingFace sentence-transformers.

    Raises RuntimeError on construction if the library is unavailable and
    no fallback is explicitly configured.  Pass ``allow_fallback=True``
    only in unit-test fixtures.
    """

    def __init__(
        self,
        metadata: EmbeddingMetadata | None = None,
        allow_fallback: bool = False,
    ) -> None:
        self._meta = metadata or DEFAULT_EMBEDDING_METADATA
        self._model = None
        self._use_fallback = False

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            self._model = SentenceTransformer(self._meta.model_name)
            logger.info("SentenceTransformer loaded: %s", self._meta.model_name)
        except Exception as exc:
            if allow_fallback:
                logger.warning(
                    "SentenceTransformer unavailable (%s); using deterministic fallback (allow_fallback=True).",
                    exc,
                )
                self._use_fallback = True
            else:
                raise RuntimeError(
                    f"Cannot load sentence-transformers model '{self._meta.model_name}': {exc}. "
                    "Install sentence-transformers or set allow_fallback=True for unit tests."
                ) from exc

    # ----- Embedder protocol -----

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
        import hashlib
        cleaned = text.strip().lower()
        if not cleaned:
            return [0.0] * self.dimension
        base_hash = hashlib.sha256(cleaned.encode("utf-8")).digest()
        vec = []
        for i in range(self.dimension):
            byte_idx = (i * 4) % len(base_hash)
            val = (int.from_bytes(base_hash[byte_idx: byte_idx + 2], "little") / 65535.0) * 2.0 - 1.0
            vec.append(val)
        norm = sum(x * x for x in vec) ** 0.5
        return [x / norm for x in vec] if norm > 0 else vec

    def embed(self, text: str) -> List[float]:
        if self._use_fallback or self._model is None:
            return self._fallback_vector(text)
        try:
            emb = self._model.encode(text, normalize_embeddings=True)
            return emb.tolist() if hasattr(emb, "tolist") else list(emb)
        except Exception as exc:
            logger.warning("Real embed failed (%s); using fallback.", exc)
            return self._fallback_vector(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self._use_fallback or self._model is None:
            return [self._fallback_vector(t) for t in texts]
        try:
            embs = self._model.encode(texts, normalize_embeddings=True)
            return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embs]
        except Exception as exc:
            logger.warning("Real batch embed failed (%s); using fallback.", exc)
            return [self._fallback_vector(t) for t in texts]


class CrossEncoderReranker:
    """Concrete Reranker wrapping HuggingFace CrossEncoder.

    Falls back to Jaccard overlap scoring when ``allow_fallback=True``;
    raises RuntimeError otherwise if the library is unavailable.
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        allow_fallback: bool = False,
    ) -> None:
        self.model_name = model_name
        self._model = None
        self._use_fallback = False

        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]
            self._model = CrossEncoder(model_name)
            logger.info("CrossEncoder loaded: %s", model_name)
        except Exception as exc:
            if allow_fallback:
                logger.warning(
                    "CrossEncoder unavailable (%s); using lexical fallback (allow_fallback=True).",
                    exc,
                )
                self._use_fallback = True
            else:
                raise RuntimeError(
                    f"Cannot load CrossEncoder model '{model_name}': {exc}. "
                    "Install sentence-transformers or set allow_fallback=True for unit tests."
                ) from exc

    # ----- Reranker protocol -----

    def rerank(
        self,
        query: str,
        items: List[CandidateItem],
    ) -> List[Tuple[CandidateItem, float]]:
        if not items:
            return []
        texts = [item.text for item in items]

        if not self._use_fallback and self._model is not None:
            try:
                pairs = [[query, t] for t in texts]
                scores = self._model.predict(pairs)
                normed = [1.0 / (1.0 + math.exp(-float(s))) for s in scores]
                return sorted(
                    zip(items, normed), key=lambda x: x[1], reverse=True
                )
            except Exception as exc:
                logger.warning("CrossEncoder.predict failed (%s); using lexical fallback.", exc)

        # Lexical Jaccard fallback
        q_tokens = set(re.findall(r"[a-zA-Z0-9]+", query.casefold()))
        results = []
        for item in items:
            t_tokens = set(re.findall(r"[a-zA-Z0-9]+", item.text.casefold()))
            if not t_tokens or not q_tokens:
                results.append((item, 0.1))
                continue
            overlap = len(q_tokens & t_tokens) / len(q_tokens | t_tokens)
            results.append((item, min(1.0, max(0.05, overlap * 2.5))))
        return sorted(results, key=lambda x: x[1], reverse=True)
