"""API-key authentication and scope resolution.

API keys authenticate an agent. They are deliberately separate from all
content-encryption and partition keys.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import secrets
from typing import Any


@dataclass(frozen=True)
class AuthContext:
    agent_id: str
    key_id: str
    scopes: frozenset[str]
    is_admin: bool = False


class ApiKeyRegistry:
    """Small registry with Redis persistence when Redis is connected.

    Only SHA-256 hashes of API keys are persisted. The raw value is returned
    only by ``create_key`` and must be delivered to the client once.
    """

    prefix = "syn_live_"
    redis_prefix = "auth:api-key:"

    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _hash(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def _redis(self):
        try:
            from kryptodb.storage.redis_client import redis_client
            return redis_client.client
        except Exception:
            return None

    def create_key(self, agent_id: str, scopes: list[str], key_id: str | None = None) -> tuple[str, dict[str, Any]]:
        raw_key = self.prefix + secrets.token_urlsafe(32)
        key_id = key_id or f"key_{secrets.token_hex(8)}"
        record = {
            "key_id": key_id,
            "agent_id": agent_id,
            "scopes": sorted(set(scopes)),
            "status": "active",
        }
        self._store(raw_key, record)
        return raw_key, record

    def _store(self, raw_key: str, record: dict[str, Any]) -> None:
        key_hash = self._hash(raw_key)
        self._memory[key_hash] = dict(record)
        client = self._redis()
        if client is not None:
            client.set(self.redis_prefix + key_hash, json.dumps(record))
            client.set(f"{self.redis_prefix}id:{record['key_id']}", key_hash)

    def _load(self, key_hash: str) -> dict[str, Any] | None:
        record = self._memory.get(key_hash)
        if record is not None:
            return dict(record)
        client = self._redis()
        if client is None:
            return None
        raw = client.get(self.redis_prefix + key_hash)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            return None
        self._memory[key_hash] = record
        return dict(record)

    def revoke(self, key_id: str) -> bool:
        changed = False
        for key_hash, record in list(self._memory.items()):
            if record.get("key_id") == key_id:
                record["status"] = "revoked"
                self._memory[key_hash] = record
                client = self._redis()
                if client is not None:
                    client.set(self.redis_prefix + key_hash, json.dumps(record))
                changed = True
        client = self._redis()
        if client is not None:
            hash_value = client.get(f"{self.redis_prefix}id:{key_id}")
            if isinstance(hash_value, bytes):
                hash_value = hash_value.decode("utf-8")
            if hash_value:
                record = self._load(hash_value)
                if record and record.get("status") != "revoked":
                    record["status"] = "revoked"
                    client.set(self.redis_prefix + hash_value, json.dumps(record))
                    changed = True
        return changed

    def list_keys(self) -> list[dict[str, Any]]:
        """Return non-secret metadata only."""
        records = list(self._memory.values())
        client = self._redis()
        if client is not None and hasattr(client, "scan_iter"):
            for redis_key in client.scan_iter(match=self.redis_prefix + "id:*"):
                key_id = redis_key.decode("utf-8") if isinstance(redis_key, bytes) else redis_key
                key_hash = client.get(redis_key)
                if isinstance(key_hash, bytes):
                    key_hash = key_hash.decode("utf-8")
                if key_hash:
                    record = self._load(key_hash)
                    if record:
                        records.append(record)
        unique = {record.get("key_id"): record for record in records if record.get("key_id")}
        return sorted(unique.values(), key=lambda record: record["key_id"])

    def authenticate(self, raw_key: str | None) -> AuthContext | None:
        if not raw_key:
            return None

        configured_admin = os.getenv("KRYPTODB_ADMIN_API_KEY")
        if configured_admin and hmac.compare_digest(raw_key, configured_admin):
            return AuthContext("admin", "admin", frozenset(), True)

        record = self._load(self._hash(raw_key))
        if not record or record.get("status") != "active":
            return None
        return AuthContext(
            agent_id=record["agent_id"],
            key_id=record["key_id"],
            scopes=frozenset(record.get("scopes", [])),
            is_admin=False,
        )

    def effective_scopes(self, context: AuthContext) -> set[str]:
        if context.is_admin:
            return set()
        from kryptodb.memory_api import get_agent
        agent = get_agent(context.agent_id)
        if not agent:
            return set()
        return set(agent.get("allowed_scopes", [])) & set(context.scopes)


api_keys = ApiKeyRegistry()


def parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()
