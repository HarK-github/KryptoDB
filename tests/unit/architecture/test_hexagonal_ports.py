"""Phase 1 Architecture Gate: Verify true hexagonal port isolation and Retriever protocol conformance."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from kryptodb.core.ports import Retriever
from kryptodb.infrastructure.neo4j.encrypted_retrieval import EncryptedRetrievalService
from kryptodb.infrastructure.neo4j.plaintext_retriever import PlaintextNeo4jRetriever

CORE_DIR = Path(__file__).resolve().parents[3] / "kryptodb" / "core"


def _iter_core_py_files():
    return sorted(p for p in CORE_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_zero_importlib_in_core() -> None:
    """Assertion 1: Zero occurrences of importlib in kryptodb/core/."""
    offenders = []
    for path in _iter_core_py_files():
        text = path.read_text(encoding="utf-8")
        if "importlib" in text:
            offenders.append(str(path.relative_to(CORE_DIR.parent.parent)))
    assert offenders == [], f"Found importlib in core/: {offenders}"


def test_zero_infrastructure_or_fastapi_in_core() -> None:
    """Assertion 2: Zero occurrences of kryptodb.infrastructure or fastapi in kryptodb/core/."""
    offenders = []
    for path in _iter_core_py_files():
        text = path.read_text(encoding="utf-8")
        if "kryptodb.infrastructure" in text or "fastapi" in text:
            offenders.append(str(path.relative_to(CORE_DIR.parent.parent)))
    assert offenders == [], f"Found forbidden infrastructure/fastapi references in core/: {offenders}"


def test_retrievers_conform_to_retriever_protocol() -> None:
    """Assertion 3: Both PlaintextNeo4jRetriever and EncryptedRetrievalService satisfy isinstance(..., Retriever)."""
    plaintext_retriever = PlaintextNeo4jRetriever(
        hybrid_engine=MagicMock(),
        traverser=MagicMock(),
        reranker=MagicMock(),
        client=MagicMock(),
    )
    encrypted_retriever = EncryptedRetrievalService(
        store=MagicMock(),
        vector_index=MagicMock(),
        embed_fn=lambda text: [0.0] * 384,
    )

    assert isinstance(plaintext_retriever, Retriever)
    assert isinstance(encrypted_retriever, Retriever)
