"""Explicit, persisted state for the temporary unencrypted bootstrap mode."""

import os

from kryptodb.storage.redis_client import redis_client

LOCK_KEY = "kryptodb:bootstrap:locked"


def configured_by_environment() -> bool:
    return os.environ.get("KRYPTODB_BOOTSTRAP_UNENCRYPTED", "false").lower() == "true"


def is_locked() -> bool:
    try:
        value = redis_client.client.get(LOCK_KEY)
    except Exception:
        return False
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return value == "1"


def is_enabled() -> bool:
    return configured_by_environment() and not is_locked()


def lock() -> None:
    redis_client.client.set(LOCK_KEY, "1")


def status() -> dict[str, bool]:
    return {"configured": configured_by_environment(), "locked": is_locked(), "enabled": is_enabled()}
