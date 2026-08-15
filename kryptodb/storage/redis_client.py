"""
Redis connection for permission bitmap + partition-key store.
"""
import redis
import os

class RedisClient:
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "redis://localhost:6380/0")
        self.client = None

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

# Global singleton
redis_client = RedisClient()
