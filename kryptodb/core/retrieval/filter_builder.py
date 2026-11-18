"""FilterBuilder re-export shim pointing to core/filters.py."""

from __future__ import annotations

import warnings
warnings.warn(
    "kryptodb.core.retrieval.filter_builder moved to kryptodb.core.filters",
    DeprecationWarning,
    stacklevel=2,
)
from kryptodb.core.filters import FilterBuilder  # noqa: F401

__all__ = ["FilterBuilder"]
