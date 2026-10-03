"""Storage bootstrap module."""
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client


def bootstrap_storage():
    """Ensure all required indices and constraints exist."""
    neo4j_client.connect()
    redis_client.connect()
    print("Storage bootstrap completed successfully.")


__all__ = ["bootstrap_storage", "neo4j_client", "redis_client"]
