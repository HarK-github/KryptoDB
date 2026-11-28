"""Infrastructure-layer concrete ML model implementations.

Preserves backward compatibility with legacy imports of
``kryptodb.infrastructure.embeddings.embedding_service``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_LEGACY_MODULE_PATH = Path(__file__).resolve().parent.parent / "embeddings.py"
_spec = importlib.util.spec_from_file_location("kryptodb.infrastructure._legacy_embeddings", _LEGACY_MODULE_PATH)
if _spec and _spec.loader:
    _legacy = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_legacy)
    EmbeddingService = _legacy.EmbeddingService
    embedding_service = _legacy.embedding_service
else:  # pragma: no cover
    EmbeddingService = None  # type: ignore[assignment]
    embedding_service = None  # type: ignore[assignment]
