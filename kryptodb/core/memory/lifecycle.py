"""Memory lifecycle state machine and transition enforcement."""

from __future__ import annotations

from typing import Dict, Set

from kryptodb.core.memory.models import MemoryStatus


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal memory lifecycle transition is attempted."""
    pass


# Strict allowed state transitions table
VALID_TRANSITIONS: Dict[MemoryStatus, Set[MemoryStatus]] = {
    MemoryStatus.PROPOSED: {MemoryStatus.ACTIVE, MemoryStatus.REVOKED},
    MemoryStatus.ACTIVE: {MemoryStatus.SUPERSEDED, MemoryStatus.REVOKED, MemoryStatus.DEPRECATED},
    MemoryStatus.SUPERSEDED: set(),  # Terminal state
    MemoryStatus.REVOKED: set(),     # Terminal state
    MemoryStatus.DEPRECATED: {MemoryStatus.REVOKED},
}


def can_transition(current: MemoryStatus, target: MemoryStatus) -> bool:
    """Checks whether transitioning from current to target status is permitted."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def transition(current: MemoryStatus, target: MemoryStatus) -> MemoryStatus:
    """Validates and applies lifecycle transition, or raises InvalidStateTransitionError."""
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"Illegal memory lifecycle transition: cannot move from {current.value} to {target.value}"
        )
    return target
