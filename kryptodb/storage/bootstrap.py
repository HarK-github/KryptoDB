"""
Schema/index creation on first run.
"""
from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.storage.redis_client import redis_client

def bootstrap_storage():
    """Ensure all required indices and constraints exist."""
    neo4j_client.connect()
    redis_client.connect()
    
    # In future phases, we will add schema bootstrap (constraints and vector indices)
    # e.g., neo4j_client.driver.execute_query("CREATE CONSTRAINT IF NOT EXISTS ...")
    
    from kryptodb.integrity.bootstrap_state import is_enabled
    bootstrap_mode = is_enabled()
    if not bootstrap_mode and neo4j_client.driver:
        query = """
        MATCH ()-[r:FACT]->()
        WHERE r.encryption_status = 'pending' OR (r.knowledge_ct IS NULL AND r.predicate IS NOT NULL)
        RETURN count(r) AS c
        """
        result = neo4j_client.execute_query_with_retry(query)
        records = result[0] if isinstance(result, tuple) else result
        if isinstance(records, (list, tuple)) and records and records[0].get("c", 0) > 0:
            raise RuntimeError(f"Cannot start in production/encrypted mode: {records[0]['c']} pending unencrypted records exist. Please enable KRYPTODB_BOOTSTRAP_UNENCRYPTED=true and migrate them first.")
    
    print("Storage bootstrap completed successfully.")
