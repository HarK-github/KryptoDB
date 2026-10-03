"""
Ephemeral Semantic Embedding Service.
INVARIANT: Embeddings are strictly ephemeral in-memory vectors computed on decrypted
candidates during an active session query. They are NEVER persisted to Neo4j, Redis,
SQLite, or disk, ensuring zero plaintext vector leakage at rest.
"""
import math
from typing import Any, Dict, List, Optional


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class EphemeralEmbeddingService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EphemeralEmbeddingService, cls).__new__(cls)
            cls._instance.model = None
        return cls._instance

    def get_model(self):
        import os
        if os.getenv("KRYPTODB_USE_REAL_EMBEDDINGS", "0") != "1":
            return None
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer('all-MiniLM-L6-v2')
            except Exception:
                self.model = None
        return self.model

    def encode(self, text: str) -> List[float]:
        """Encodes text to float vector. Uses fast word-hash embedding unless real embeddings enabled."""
        model = self.get_model()
        if model is not None:
            return model.encode(text).tolist()
        
        # Fast, zero-dependency word-hash vector
        import hashlib
        dim = 64
        vec = [0.0] * dim
        words = text.lower().split()
        if not words:
            return vec
        for word in words:
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if (h // dim) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def rank_facts_ephemeral(self, query: str, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Ranks decrypted in-memory facts by cosine similarity against the query.
        Vectors exist ONLY within this function scope and are immediately garbage-collected.
        """
        if not facts:
            return []

        q_vec = self.encode(query)
        scored = []
        for f in facts:
            # Derive text representation from decrypted fact fields
            text = f"{f.get('subject', '')} {f.get('predicate', '')} {f.get('object', '')} {f.get('description', '')}".strip()
            score = cosine_similarity(q_vec, self.encode(text))
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored]


# Aliases for backward compatibility
EmbeddingService = EphemeralEmbeddingService
embedding_service = EphemeralEmbeddingService()
