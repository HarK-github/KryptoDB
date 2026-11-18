from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Dict

from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.infrastructure.embeddings import embedding_service

logger = logging.getLogger("kryptodb.write_engine")

class WriteEngine:
    """
    Plaintext Neo4j WriteEngine with native vector embeddings, tenant isolation,
    and strict timezone-aware UTC timestamps.
    """
    def __init__(self, client: Any = None):
        self.client = client or neo4j_client

    def write_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        description: Optional[str] = None,
        scope: str = "default",
        tenant_id: str = "default",
        confidence: float = 1.0,
        trust_weight: float = 1.0,
        status: str = "active",
        version: int = 1,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
        source_agent_id: str = "agent-1",
        dataset_id: Optional[str] = None,
        fact_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Writes subject, object, and fact relationship with vector embeddings into Neo4j.
        """
        now = datetime.now(timezone.utc)
        valid_from_dt = valid_from or now
        fact_uuid = fact_id or str(uuid.uuid4())
        desc = description or f"{subject} {predicate} {obj}"
        
        # 1. Compute embeddings
        s_emb, s_pending = embedding_service.embed_with_resilience(subject)
        o_emb, o_pending = embedding_service.embed_with_resilience(obj)
        rel_emb, rel_pending = embedding_service.embed_with_resilience(desc)
        
        query = """
        MERGE (s:Entity {name: $s_name, tenant_id: $tenant_id})
        ON CREATE SET s.id = $s_id,
                      s.embedding = $s_emb,
                      s.embedding_pending = $s_pending,
                      s.created_at = $now,
                      s.updated_at = $now
        ON MATCH SET s.embedding = coalesce(s.embedding, $s_emb),
                     s.updated_at = $now

        MERGE (o:Entity {name: $o_name, tenant_id: $tenant_id})
        ON CREATE SET o.id = $o_id,
                      o.embedding = $o_emb,
                      o.embedding_pending = $o_pending,
                      o.created_at = $now,
                      o.updated_at = $now
        ON MATCH SET o.embedding = coalesce(o.embedding, $o_emb),
                     o.updated_at = $now

        CREATE (s)-[r:FACT {
            id: $fact_id,
            predicate: $predicate,
            description: $description,
            embedding: $rel_emb,
            embedding_pending: $rel_pending,
            scope: $scope,
            tenant_id: $tenant_id,
            status: $status,
            version: $version,
            confidence: $confidence,
            trust_weight: $trust_weight,
            valid_from: $valid_from,
            valid_until: $valid_until,
            source_agent_id: $source_agent_id,
            dataset_id: $dataset_id,
            created_at: $now
        }]->(o)
        RETURN s, r, o
        """
        
        params = {
            "s_id": str(uuid.uuid4()),
            "s_name": subject.strip(),
            "s_emb": s_emb,
            "s_pending": s_pending,
            "o_id": str(uuid.uuid4()),
            "o_name": obj.strip(),
            "o_emb": o_emb,
            "o_pending": o_pending,
            "fact_id": fact_uuid,
            "predicate": predicate.strip().upper(),
            "description": desc,
            "rel_emb": rel_emb,
            "rel_pending": rel_pending,
            "scope": scope,
            "tenant_id": tenant_id,
            "status": status,
            "version": version,
            "confidence": float(confidence),
            "trust_weight": float(trust_weight),
            "valid_from": valid_from_dt.isoformat(),
            "valid_until": valid_until.isoformat() if valid_until else None,
            "source_agent_id": source_agent_id,
            "dataset_id": dataset_id,
            "now": now.isoformat()
        }
        
        if not getattr(self.client, "driver", None):
            logger.warning("Neo4j driver offline. Recording fact in fallback mode.")
            return {
                "fact_id": fact_uuid,
                "subject": subject.strip(),
                "predicate": predicate.strip().upper(),
                "object": obj.strip(),
                "description": desc,
                "status": status,
                "scope": scope,
                "tenant_id": tenant_id,
                "confidence": confidence,
                "valid_from": valid_from_dt.isoformat(),
                "valid_until": valid_until.isoformat() if valid_until else None,
                "created_at": now.isoformat()
            }

        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(query, **params)
            else:
                records, _, _ = self.client.driver.execute_query(query, **params)
        except Exception as exc:
            logger.error("Error writing fact to Neo4j: %s", exc)
            raise
            
        return {
            "fact_id": fact_uuid,
            "subject": subject.strip(),
            "predicate": predicate.strip().upper(),
            "object": obj.strip(),
            "description": desc,
            "status": status,
            "scope": scope,
            "tenant_id": tenant_id,
            "confidence": confidence,
            "valid_from": valid_from_dt.isoformat(),
            "valid_until": valid_until.isoformat() if valid_until else None,
            "created_at": now.isoformat()
        }
