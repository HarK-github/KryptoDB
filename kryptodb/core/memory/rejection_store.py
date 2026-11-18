"""Tracks and persists rejected fact proposals to prevent recurring re-proposals."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional, Set, Union

logger = logging.getLogger("kryptodb.core.memory.rejections")

_REJECTED_PROPOSALS: Set[str] = set()


def compute_proposal_hash(
    subject: Any = None,
    predicate: Any = None,
    obj: Any = None,
    tenant_id: str = "default",
    **kwargs: Any,
) -> str:
    """Computes a stable hash representing a proposed semantic triple."""
    if isinstance(subject, dict):
        d = subject
        s = d.get("subject") or ""
        p = d.get("predicate") or ""
        o = d.get("obj") or d.get("object") or ""
        t = d.get("tenant_id") or d.get("scope") or tenant_id
    elif isinstance(predicate, dict):
        t = str(subject or "default")
        d = predicate
        s = d.get("subject") or ""
        p = d.get("predicate") or ""
        o = d.get("obj") or d.get("object") or ""
    else:
        s = str(subject or "")
        p = str(predicate or "")
        o = str(obj or "")
        t = str(tenant_id)
    canonical = f"{str(t).strip().casefold()}:{s.strip().casefold()}:{p.strip().casefold()}:{o.strip().casefold()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_rejection(
    subject: Any = None,
    predicate: Any = None,
    obj: Any = None,
    tenant_id: str = "default",
    **kwargs: Any,
) -> str:
    """Marks a proposed fact as rejected by human reviewer."""
    h = compute_proposal_hash(subject, predicate, obj, tenant_id, **kwargs)
    _REJECTED_PROPOSALS.add(h)
    logger.info("Recorded proposal rejection hash: %s", h)
    return h


def is_proposal_rejected(
    subject: Any = None,
    predicate: Any = None,
    obj: Any = None,
    tenant_id: str = "default",
    **kwargs: Any,
) -> bool:
    """Checks whether a proposal has previously been rejected."""
    h = compute_proposal_hash(subject, predicate, obj, tenant_id, **kwargs)
    return h in _REJECTED_PROPOSALS


def clear_rejections() -> None:
    """Clears rejection cache (primarily for tests)."""
    _REJECTED_PROPOSALS.clear()
