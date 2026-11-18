"""MultiHopTraverser re-export shim pointing to core/retrieval/traversal.py."""

from __future__ import annotations

import warnings
warnings.warn(
    "kryptodb.core.retrieval.multihop moved to kryptodb.core.retrieval.traversal",
    DeprecationWarning,
    stacklevel=2,
)
from kryptodb.core.retrieval.traversal import MultiHopTraverser, multihop_traverser  # noqa: F401

__all__ = ["MultiHopTraverser", "multihop_traverser"]
