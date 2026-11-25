"""
Neo4j Community Edition connection.
"""
try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None  # type: ignore
import os

class Neo4jClient:
    def __init__(self):
        self.uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "password")
        self.driver = None

    def connect(self, retries=15, delay=3):
        import time
        import logging
        if not self.driver:
            for attempt in range(retries):
                try:
                    self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                    self.driver.verify_connectivity()
                    return
                except Exception as e:
                    if self.driver:
                        self.driver.close()
                        self.driver = None
                    if attempt == retries - 1:
                        raise e
                    logging.warning(f"Neo4j connection failed, retrying in {delay}s...")
                    time.sleep(delay)

    def close(self):
        if self.driver:
            self.driver.close()
            self.driver = None

    def check_health(self) -> bool:
        if not self.driver:
            return False
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False
            
    def execute_query_with_retry(self, query: str, max_retries: int = 5, base_delay: float = 0.5, trace_id: str | None = None, **kwargs):
        """
        Executes a Cypher query with an explicit exponential backoff retry loop.
        This handles Write-Time Deadlocks (TransientError) during high-concurrency multi-agent writes.
        """
        from neo4j.exceptions import TransientError
        import time
        import random
        import logging
        import json

        if not self.driver:
            raise RuntimeError("Neo4j driver not connected.")

        params = dict(kwargs)
        if trace_id is None and "trace_id" in params:
            trace_id = params.pop("trace_id")

        if trace_id:
            logging.info(json.dumps({
                "event": "neo4j_call",
                "call_type": "query",
                "trace_id": trace_id,
                "query_prefix": query.strip()[:80]
            }))
            
        for attempt in range(max_retries):
            try:
                # We use execute_query which handles the managed transaction, 
                # but we wrap it to ensure strict application-level retry/backoff control.
                return self.driver.execute_query(query, parameters_=params)
            except TransientError as e:
                if attempt == max_retries - 1:
                    logging.error(f"Max retries ({max_retries}) exhausted for TransientError deadlock.")
                    raise e
                
                # Exponential backoff with jitter
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.2)
                logging.warning(f"Deadlock (TransientError) detected on attempt {attempt+1}. Retrying in {delay:.2f}s...")
                time.sleep(delay)
            except Exception as e:
                # Non-transient errors should bubble up immediately
                raise e

    def execute_write_transaction(self, tx_func, max_retries: int = 5, base_delay: float = 0.5, trace_id: str | None = None):
        """
        Executes a transaction function containing multiple Cypher commands inside
        a single atomic Neo4j managed write transaction.
        """
        from neo4j.exceptions import TransientError
        import time
        import random
        import logging
        import json

        if not self.driver:
            raise RuntimeError("Neo4j driver not connected.")

        if trace_id:
            logging.info(json.dumps({
                "event": "neo4j_call",
                "call_type": "write_tx",
                "trace_id": trace_id
            }))

        for attempt in range(max_retries):
            try:
                with self.driver.session() as session:
                    return session.execute_write(tx_func)
            except TransientError as e:
                if attempt == max_retries - 1:
                    logging.error(f"Max retries ({max_retries}) exhausted in write transaction for TransientError deadlock.")
                    raise e
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.2)
                logging.warning(f"Deadlock (TransientError) in transaction on attempt {attempt+1}. Retrying in {delay:.2f}s...")
                time.sleep(delay)
            except Exception as e:
                raise e

# Global singleton
neo4j_client = Neo4jClient()
