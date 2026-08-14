"""
Time-Partitioned Cold Storage for Superseded Edges.
Implements the eviction worker for migrating old immutable edges to SQLite.
"""
from synapse.storage.neo4j_client import neo4j_client
from synapse.integrity.audit_log import audit_logger
from datetime import datetime, timezone, timedelta
import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

class GraphArchiver:
    def __init__(self, ttl_days: int = 30):
        self.ttl_days = ttl_days

    def _now(self):
        return datetime.now(timezone.utc)

    def run_eviction(self) -> int:
        """
        Finds edges where valid_to < (now - ttl_days).
        Appends them to SQLite cold storage, then hard DELETEs them from Neo4j.
        """
        if not neo4j_client.driver:
            return 0
            
        threshold = (self._now() - timedelta(days=self.ttl_days)).isoformat()
        
        # 1. Query for edges to evict
        query = """
        MATCH (s)-[r:FACT]->(o)
        WHERE r.valid_to IS NOT NULL AND r.valid_to < $threshold
        RETURN r.id AS edge_id, r.permission_scope AS scope, r.valid_to AS valid_to, 
               properties(r) AS props, properties(s) AS s_props, properties(o) AS o_props
        LIMIT 1000
        """
        try:
            records, _, _ = neo4j_client.execute_query_with_retry(query, threshold=threshold)
            
            if not records:
                return 0
                
            archived_count = 0
            for record in records:
                edge_id = record["edge_id"]
                
                # 2. Serialize and append to SQLite Audit Log / Archive
                archive_entry = {
                    "event": "COLD_STORAGE_MIGRATION",
                    "edge_id": edge_id,
                    "archived_at": self._now().isoformat(),
                    "edge_properties": record["props"],
                    "subject_properties": record["s_props"],
                    "object_properties": record["o_props"]
                }
                
                audit_logger.log_write(
                    actor="system_archiver",
                    action="archive_edge",
                    target_id=edge_id,
                    scope=record["scope"],
                    result="SUCCESS",
                    details=json.dumps(archive_entry, default=str)
                )
                
                # 3. Hard DELETE from Neo4j
                delete_query = """
                MATCH ()-[r:FACT {id: $edge_id}]->()
                DELETE r
                """
                neo4j_client.execute_query_with_retry(delete_query, edge_id=edge_id)
                archived_count += 1
                
            logger.info(f"Evicted {archived_count} old edges to cold storage.")
            return archived_count
            
        except Exception as e:
            logger.error(f"Failed to run eviction worker: {e}")
            return 0

archiver = GraphArchiver()


class ColdStorageArchiver:
    """SQLite cold-storage adapter retained for the storage API/tests."""

    def __init__(self, db_path: str = "data/cold_storage.db"):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS archived_edges (
                    id TEXT PRIMARY KEY,
                    valid_to TEXT,
                    superseded_by TEXT,
                    payload TEXT
                )"""
            )

    def run_sweep(self, days_old: int = 30) -> int:
        if not neo4j_client.driver:
            return 0
        threshold = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        query = """
        MATCH ()-[r:FACT]->()
        WHERE r.valid_to IS NOT NULL AND r.valid_to < $threshold
        RETURN r.id AS id, r.valid_to AS valid_to,
               r.superseded_by AS superseded_by, properties(r) AS props
        """
        records, _, _ = neo4j_client.execute_query_with_retry(query, threshold=threshold)
        archived = 0
        with sqlite3.connect(self.db_path) as conn:
            for record in records:
                conn.execute(
                    "INSERT OR REPLACE INTO archived_edges (id, valid_to, superseded_by, payload) VALUES (?, ?, ?, ?)",
                    (record["id"], record["valid_to"], record.get("superseded_by"), json.dumps(record.get("props"), default=str)),
                )
                neo4j_client.execute_query_with_retry(
                    "MATCH ()-[r:FACT {id: $id}]->() DELETE r",
                    id=record["id"],
                )
                archived += 1
        return archived
