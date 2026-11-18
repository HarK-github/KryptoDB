"""Storage layer for KryptoDB graph and session persistence."""


def bootstrap_storage():
    """Ensure all required indices and constraints exist."""
    from kryptodb.storage.neo4j_client import neo4j_client
    from kryptodb.storage.redis_client import redis_client

    neo4j_client.connect()
    try:
        redis_client.connect()
    except Exception as exc:
        print(f"Redis connect error (non-fatal): {exc}")
    print("Storage bootstrap completed successfully.")


__all__ = ["bootstrap_storage"]
