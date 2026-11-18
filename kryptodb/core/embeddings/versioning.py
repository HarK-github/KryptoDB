"""Embedding versioning and compatibility enforcement."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingMetadata:
    model_name: str = "all-MiniLM-L6-v2"
    model_version: str = "1.0.0"
    dimension: int = 384


DEFAULT_EMBEDDING_METADATA = EmbeddingMetadata()


def validate_embedding_compatibility(
    expected: EmbeddingMetadata,
    actual: EmbeddingMetadata
) -> bool:
    """Verifies that vector dimensions and model versions are mathematically compatible.
    
    Mixing vectors from different models or dimensions corrupts cosine similarity ranking.
    """
    if expected.dimension != actual.dimension:
        raise ValueError(
            f"Embedding dimension mismatch: expected {expected.dimension}, got {actual.dimension}"
        )
    if expected.model_name != actual.model_name:
        raise ValueError(
            f"Embedding model mismatch: expected {expected.model_name}, got {actual.model_name}"
        )
    return True
