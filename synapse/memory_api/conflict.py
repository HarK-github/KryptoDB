from synapse.storage.neo4j_client import neo4j_client
from synapse.graph.schema import Edge
from synapse.integrity.partition_crypto import Encryptor

def detect_conflict(subject: str, predicate: str, obj: str, permission_scope: str) -> list[Edge]:
    """Two-tier conflict detection. Returns live conflicting edges."""
    
    # Tier 1: Exact subject + predicate match
    encryptor = Encryptor()
    subject_idx = encryptor.compute_search_idx(permission_scope, "subject", subject)
    predicate_idx = encryptor.compute_search_idx(permission_scope, "predicate", predicate)
    query = """
    MATCH (s)-[r]->(o)
    WHERE (
        (r.subject_search_idx = $subject_idx AND r.predicate_search_idx = $predicate_idx)
        OR (s.id = $subject AND r.predicate = $predicate)
      )
      AND r.permission_scope = $scope
      AND r.valid_to IS NULL
    RETURN s, r, o
    """
    records, _, _ = neo4j_client.execute_query_with_retry(
        query,
        subject=subject,
        predicate=predicate,
        subject_idx=subject_idx,
        predicate_idx=predicate_idx,
        scope=permission_scope,
    )
    
    edges = []
    for record in records:
        r = record["r"]
        s = record["s"]
        o = record["o"]
        edges.append(Edge(
            id=r["id"],
            type=r["type"] if "type" in r else "FACT",
            predicate=predicate,
            permission_scope=r["permission_scope"],
            source_agent_id=r.get("source_agent_id", "encrypted"),
            subject_id=subject,
            object_id=obj,
            trust_weight=r["trust_weight"],
            valid_from=r["valid_from"],
            valid_to=r["valid_to"],
            ingested_at=r["ingested_at"],
            unlock_at=r.get("unlock_at"),
            derived_from=r.get("derived_from") or [],
            superseded_by=r.get("superseded_by")
        ))
        
    if edges:
        return edges
        
    # Tier 2: Fuzzy match (Not implemented for this prototype, return empty)
    # This would embed the subject and cosine-similarity check against Neo4j Vector Index
    
    return []
