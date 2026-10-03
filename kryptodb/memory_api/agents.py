"""Backward-compatibility facade for agent registry."""
from kryptodb.memory_api.service import get_agent, _DEFAULT_AGENTS

__all__ = ["get_agent", "_DEFAULT_AGENTS"]
