"""Unified Memory API for KryptoDB."""

from .service import (
    MemoryService,
    memory_service,
    get_agent,
    get_all_scopes,
    admit_fact,
    detect_conflict,
    propose_fact,
)

__all__ = [
    "MemoryService",
    "memory_service",
    "get_agent",
    "get_all_scopes",
    "admit_fact",
    "detect_conflict",
    "propose_fact",
]
