import contextlib
import os
import time
import uuid
import redis


class WriteLockError(Exception):
    """Raised when a concurrent write lock on (subject, predicate) cannot be acquired."""
    pass


class RedisClient:
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.client = None
        self._memory_locks: dict[str, tuple[str, float]] = {}

    def connect(self):
        if not self.client:
            self.client = redis.from_url(self.url, decode_responses=False)
            self.client.ping()

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def check_health(self) -> bool:
        if not self.client:
            return False
        try:
            return self.client.ping()
        except Exception:
            return False

    def _acquire_memory_lock(self, lock_key: str, token: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        existing = self._memory_locks.get(lock_key)
        if existing:
            _, exp = existing
            if now < exp:
                return False
        self._memory_locks[lock_key] = (token, now + ttl_seconds)
        return True

    def _release_memory_lock(self, lock_key: str, token: str):
        existing = self._memory_locks.get(lock_key)
        if existing and existing[0] == token:
            self._memory_locks.pop(lock_key, None)

    @contextlib.contextmanager
    def acquire_lock(self, resource_key: str, ttl_seconds: int = 10, timeout_seconds: float = 0.0):
        """
        Acquires an exclusive lock for (subject, predicate) across writes.
        Fails immediately with WriteLockError if timeout_seconds=0.0 and another write holds the lock.
        """
        token = str(uuid.uuid4())
        lock_key = f"lock:write:{resource_key}"
        start_time = time.monotonic()
        acquired = False

        while True:
            if self.client:
                try:
                    res = self.client.set(lock_key, token, nx=True, ex=ttl_seconds)
                    acquired = bool(res)
                except Exception:
                    acquired = self._acquire_memory_lock(lock_key, token, ttl_seconds)
            else:
                acquired = self._acquire_memory_lock(lock_key, token, ttl_seconds)

            if acquired:
                break

            if time.monotonic() - start_time >= timeout_seconds:
                raise WriteLockError(f"Concurrent write lock held for {resource_key}")

            time.sleep(0.05)

        try:
            yield token
        finally:
            if self.client:
                try:
                    # Release only if matching token
                    val = self.client.get(lock_key)
                    if val and (val == token.encode() or val == token):
                        self.client.delete(lock_key)
                except Exception:
                    self._release_memory_lock(lock_key, token)
            else:
                self._release_memory_lock(lock_key, token)


# Global singleton
redis_client = RedisClient()
