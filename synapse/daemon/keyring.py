"""
In-memory session keyring. Never persisted.
Holds active partition keys and agent scope.
"""
from typing import Set

class SessionKeyring:
    def __init__(self):
        self._active_partitions: Set[str] = set()
        self._keys: dict[str, bytes] = {}

    def get_active_partitions(self) -> Set[str]:
        return self._active_partitions

    def set_active_partitions(self, partitions: Set[str]):
        self._active_partitions = partitions

    def add_key(self, partition: str, key: bytes):
        self._keys[partition] = key
        
    def get_key(self, partition: str) -> bytes | None:
        return self._keys.get(partition)
        
    def clear(self):
        from synapse.integrity.partition_crypto import clear_crypto_cache
        self._active_partitions.clear()
        self._keys.clear()
        clear_crypto_cache()

# Global singleton for the daemon
keyring = SessionKeyring()
