"""Per-client session state. Session keys never live in a process-global map."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import secrets
@dataclass
class SessionState:
    session_id: str
    agent_id: str
    key_id: str
    scopes: set[str]
    keys: dict[str, bytes] = field(default_factory=dict)
    expires_at: datetime | None = None

    def is_expired(self) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc) >= self.expires_at

    def clear(self) -> None:
        self.keys.clear()

    @property
    def _keys(self) -> dict[str, bytes]:
        """Compatibility view for the existing retrieval code."""
        return self.keys

    def get_key(self, partition: str) -> bytes | None:
        return self.keys.get(partition)


class SessionStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, SessionState] = {}

    def create(self, agent_id: str, key_id: str, scopes: set[str]) -> SessionState:
        session = SessionState(
            session_id="sess_" + secrets.token_urlsafe(24),
            agent_id=agent_id,
            key_id=key_id,
            scopes=set(scopes),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> SessionState | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if not session:
            return None
        if session.is_expired():
            session.clear()
            self._sessions.pop(session_id, None)
            return None
        return session

    def revoke(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.clear()
        self._sessions.pop(session_id, None)
        return True

    def revoke_for_key(self, key_id: str) -> None:
        for session_id, session in list(self._sessions.items()):
            if session.key_id == key_id:
                session.clear()
                self._sessions.pop(session_id, None)

    def revoke_for_agent(self, agent_id: str) -> None:
        for session_id, session in list(self._sessions.items()):
            if session.agent_id == agent_id:
                session.clear()
                self._sessions.pop(session_id, None)


sessions = SessionStore()


class SessionKeyring:
    """In-memory process-level session keyring holding active partition keys."""
    def __init__(self):
        self._active_partitions: set[str] = set()
        self._keys: dict[str, bytes] = {}

    def get_active_partitions(self) -> set[str]:
        return self._active_partitions

    def set_active_partitions(self, partitions: set[str]):
        self._active_partitions = set(partitions)

    def add_key(self, partition: str, key: bytes):
        self._keys[partition] = key
        
    def get_key(self, partition: str) -> bytes | None:
        return self._keys.get(partition)
        
    def remove_key(self, partition: str):
        self._keys.pop(partition, None)
        self._active_partitions.discard(partition)
        from kryptodb.integrity.partition_crypto import clear_crypto_cache
        clear_crypto_cache()

    def revoke_partitions(self, partitions: set[str] | list[str]):
        for p in partitions:
            self.remove_key(p)

    def clear(self):
        from kryptodb.integrity.partition_crypto import clear_crypto_cache
        self._active_partitions.clear()
        self._keys.clear()
        clear_crypto_cache()


# Global singleton for the daemon process
keyring = SessionKeyring()
