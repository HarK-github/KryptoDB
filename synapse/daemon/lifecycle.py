"""
Liveness checks, lazy start routines.
"""
from synapse.storage.neo4j_client import neo4j_client
from synapse.storage.redis_client import redis_client
from synapse.storage.bootstrap import bootstrap_storage
import logging

logger = logging.getLogger(__name__)

def startup_event():
    logger.info("Starting Synapse Daemon...")
    try:
        bootstrap_storage()
        logger.info("Storage connected and bootstrapped.")
    except Exception as e:
        logger.error(f"Failed to connect to storage: {e}")
        raise

def shutdown_event():
    logger.info("Shutting down Synapse Daemon...")
    neo4j_client.close()
    redis_client.close()

def check_health() -> dict:
    neo4j_up = False
    try:
        neo4j_up = neo4j_client.check_health()
    except Exception:
        pass
        
    redis_up = False
    try:
        redis_up = redis_client.check_health()
    except Exception:
        pass

    return {
        "status": "up" if neo4j_up and redis_up else "down",
        "neo4j": "up" if neo4j_up else "down",
        "redis": "up" if redis_up else "down"
    }
