"""Cypher query constants for KryptoDB Neo4j infrastructure."""

from __future__ import annotations

WRITE_FACT_QUERY = """
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

GET_FACT_QUERY = """
MATCH (s:Entity)-[r:FACT {id: $fact_id}]->(o:Entity)
WHERE {filter_clause}
RETURN r, s.name AS subject, o.name AS object
"""

FIND_ACTIVE_BY_SUBJECT_PREDICATE_QUERY = """
MATCH (s:Entity {name: $subject, tenant_id: $tenant_id})-[r:FACT {predicate: $predicate}]->(o:Entity)
WHERE {filter_clause}
RETURN r, s.name AS subject, o.name AS object
"""

SUPERSEDE_FACT_QUERY = """
MATCH ()-[r:FACT {id: $old_id, tenant_id: $tenant_id}]->()
SET r.status = 'superseded',
    r.valid_until = $now,
    r.superseded_by = $new_id
RETURN r.id AS id
"""

ATOMIC_SUPERSEDE_QUERY = """
MATCH ()-[old_r:FACT {id: $old_id, tenant_id: $tenant_id}]->()
SET old_r.status = 'superseded',
    old_r.valid_until = $now,
    old_r.superseded_by = $fact_id

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
    created_at: $now,
    previous_version_id: $old_id
}]->(o)
RETURN s, r, o
"""

REVOKE_FACT_QUERY = """
MATCH ()-[r:FACT {id: $fact_id, tenant_id: $tenant_id}]->()
SET r.status = 'revoked',
    r.valid_until = $now
RETURN r.id AS id
"""

FIND_ENTITY_QUERY = """
MATCH (e:Entity {name: $name, tenant_id: $tenant_id})
RETURN e.id AS id, e.name AS name, e.tenant_id AS tenant_id, e.created_at AS created_at, e.embedding AS embedding
"""

SEARCH_FACT_VECTOR_QUERY = """
CALL db.index.vector.queryRelationships('fact_embedding_idx', $top_k, $query_embedding)
YIELD relationship AS r, score
WHERE {filter_clause}
MATCH (s:Entity)-[r]->(o:Entity)
RETURN r, s.name AS subject, o.name AS object, score
"""

SEARCH_ENTITY_VECTOR_QUERY = """
CALL db.index.vector.queryNodes('entity_embedding_idx', $top_k, $query_embedding)
YIELD node AS e, score
WHERE e.tenant_id = $tenant_id
RETURN e.id AS id, e.name AS name, score
"""

SEARCH_FACT_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryRelationships('fact_text_ft', $query_text)
YIELD relationship AS r, score
WHERE {filter_clause}
MATCH (s:Entity)-[r]->(o:Entity)
RETURN r, s.name AS subject, o.name AS object, score
"""

SEARCH_ENTITY_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('entity_name_ft', $query_text)
YIELD node AS e, score
WHERE e.tenant_id = $tenant_id
RETURN e.id AS id, e.name AS name, score
"""

TRAVERSE_2HOP_QUERY = """
MATCH (seed:Entity)
WHERE seed.name IN $seed_names AND seed.tenant_id = $tenant_id
MATCH (seed)-[r1:FACT]->(hop1:Entity)
WHERE {filter_clause_r1}
OPTIONAL MATCH (hop1)-[r2:FACT]->(hop2:Entity)
WHERE {filter_clause_r2} AND hop2 <> seed AND hop2 <> hop1
WITH seed, r1, hop1, r2, hop2
RETURN seed.name AS seed_name,
       r1,
       hop1.name AS hop1_name,
       r2,
       case when hop2 is null then null else hop2.name end AS hop2_name
LIMIT $max_results
"""

APPEND_EVENT_QUERY = """
CREATE (ev:Event {
    id: $id,
    session_id: $session_id,
    event_type: $event_type,
    content: $content,
    tenant_id: $tenant_id,
    scope: $scope,
    agent_id: $agent_id,
    user_id: $user_id,
    timestamp: $timestamp
})
RETURN ev.id AS id
"""

LIST_EVENTS_BY_SESSION_QUERY = """
MATCH (ev:Event {session_id: $session_id, tenant_id: $tenant_id})
RETURN ev
ORDER BY ev.timestamp ASC
"""

STORE_EPISODE_SUMMARY_QUERY = """
CREATE (ep:Episode {
    id: $id,
    session_id: $session_id,
    summary: $summary,
    topics: $topics,
    tenant_id: $tenant_id,
    scope: $scope,
    timestamp: $timestamp
})
RETURN ep.id AS id
"""
