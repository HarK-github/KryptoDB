"""Backward-compatibility facade for session keyring."""
from kryptodb.daemon.sessions import SessionKeyring, keyring

__all__ = ["SessionKeyring", "keyring"]
