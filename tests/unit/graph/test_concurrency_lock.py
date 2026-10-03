import pytest
import threading
from kryptodb.storage.redis_client import redis_client, WriteLockError


def test_concurrency_lock_prevents_duplicate_writer():
    """Verify that acquiring a write lock on (subject, predicate) prevents concurrent writes."""
    scope = "PROJECT:alpha"
    subject = "server-01"
    predicate = "STATUS"
    resource_key = f"{scope}:{subject}:{predicate}"

    lock_acquired_by_t1 = threading.Event()
    release_t1 = threading.Event()
    t2_failed_with_conflict = threading.Event()

    def writer_1():
        with redis_client.acquire_lock(resource_key, ttl_seconds=5, timeout_seconds=0.0):
            lock_acquired_by_t1.set()
            release_t1.wait(timeout=2.0)

    def writer_2():
        lock_acquired_by_t1.wait(timeout=2.0)
        try:
            with redis_client.acquire_lock(resource_key, ttl_seconds=5, timeout_seconds=0.0):
                pass
        except WriteLockError:
            t2_failed_with_conflict.set()

    t1 = threading.Thread(target=writer_1)
    t2 = threading.Thread(target=writer_2)

    t1.start()
    t2.start()

    t2.join(timeout=3.0)
    release_t1.set()
    t1.join(timeout=3.0)

    assert t2_failed_with_conflict.is_set(), "Writer 2 must fail immediately with WriteLockError when Writer 1 holds lock"


def test_concurrency_lock_releases_after_completion():
    """Verify that lock is freed and subsequent writer can acquire it once previous write finishes."""
    resource_key = "PROJECT:alpha:alice:OWNS"

    with redis_client.acquire_lock(resource_key, ttl_seconds=2):
        pass

    # Should be acquired without error now
    with redis_client.acquire_lock(resource_key, ttl_seconds=2):
        pass
