"""Cypher query constants for KryptoDB Neo4j infrastructure."""

from __future__ import annotations

FIND_BY_IDEMPOTENCY_KEY_QUERY = """
MATCH (s:Entity)-[r:FACT {idempotency_key: $idempotency_key, tenant_id: $tenant_id}]->(o:Entity)
WHERE r.status = 'active'
RETURN r, s.name AS subject, o.name AS object
LIMIT 1
"""

WRITE_FACT_QUERY = """
MERGE (s:Entity {norm_name: $s_norm_name, tenant_id: $tenant_id})
ON CREATE SET s.id = $s_id,
              s.name = $s_name,
              s.embedding = $s_emb,
              s.embedding_pending = $s_pending,
              s.created_at = $now,
              s.updated_at = $now
ON MATCH SET s.name = coalesce(s.name, $s_name),
             s.embedding = coalesce(s.embedding, $s_emb),
             s.updated_at = $now
SET s._write_lock = true

MERGE (o:Entity {norm_name: $o_norm_name, tenant_id: $tenant_id})
ON CREATE SET o.id = $o_id,
              o.name = $o_name,
              o.embedding = $o_emb,
              o.embedding_pending = $o_pending,
              o.created_at = $now,
              o.updated_at = $now
ON MATCH SET o.name = coalesce(o.name, $o_name),
             o.embedding = coalesce(o.embedding, $o_emb),
             o.updated_at = $now
REMOVE s._write_lock

CREATE (s)-[r:FACT {
    id: $fact_id,
    idempotency_key: $idempotency_key,
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
    recorded_from: $now,
    recorded_to: NULL,
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
MATCH (s:Entity {tenant_id: $tenant_id})-[r:FACT {predicate: $predicate}]->(o:Entity)
WHERE (s.norm_name = $norm_subject OR toLower(s.name) = $norm_subject)
  AND {filter_clause}
RETURN r, s.name AS subject, o.name AS object
"""

SUPERSEDE_FACT_QUERY = """
MATCH ()-[r:FACT {id: $old_id, tenant_id: $tenant_id}]->()
SET r._lock = true
WITH r
WHERE r.status = 'active'
SET r.status = 'superseded',
    r.valid_until = $now,
    r.recorded_to = $now,
    r.superseded_by = $new_id
REMOVE r._lock
RETURN r.id AS id
"""

ATOMIC_SUPERSEDE_QUERY = """
MATCH ()-[old_r:FACT {id: $old_id, tenant_id: $tenant_id}]->()
SET old_r._lock = true
WITH old_r
WHERE old_r.status = 'active' AND ($scope IS NULL OR old_r.scope = $scope)
SET old_r.status = 'superseded',
    old_r.valid_until = $now,
    old_r.recorded_to = $now,
    old_r.superseded_by = $fact_id
REMOVE old_r._lock

MERGE (s:Entity {norm_name: $s_norm_name, tenant_id: $tenant_id})
ON CREATE SET s.id = $s_id,
              s.name = $s_name,
              s.embedding = $s_emb,
              s.embedding_pending = $s_pending,
              s.created_at = $now,
              s.updated_at = $now
ON MATCH SET s.name = coalesce(s.name, $s_name),
             s.embedding = coalesce(s.embedding, $s_emb),
             s.updated_at = $now

MERGE (o:Entity {norm_name: $o_norm_name, tenant_id: $tenant_id})
ON CREATE SET o.id = $o_id,
              o.name = $o_name,
              o.embedding = $o_emb,
              o.embedding_pending = $o_pending,
              o.created_at = $now,
              o.updated_at = $now
ON MATCH SET o.name = coalesce(o.name, $o_name),
             o.embedding = coalesce(o.embedding, $o_emb),
             o.updated_at = $now

CREATE (s)-[r:FACT {
    id: $fact_id,
    idempotency_key: $idempotency_key,
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
    recorded_from: $now,
    recorded_to: NULL,
    source_agent_id: $source_agent_id,
    dataset_id: $dataset_id,
    created_at: $now,
    previous_version_id: $old_id
}]->(o)
RETURN s, r, o
"""

REVOKE_FACT_QUERY = """
MATCH ()-[r:FACT {id: $fact_id, tenant_id: $tenant_id}]->()
WHERE r.status = 'active'
  AND ($is_system = true OR r.scope IN $active_scopes)
SET r.status = 'revoked',
    r.description = NULL,
    r.embedding = NULL,
    r.predicate = 'REVOKED',
    r.valid_until = $now,
    r.recorded_to = $now
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

REVOKE_BY_USER_QUERY = """
MATCH ()-[r:FACT {tenant_id: $tenant_id}]->()
WHERE (r.user_id = $user_id OR r.source_agent_id = $user_id)
  AND ($is_system = true OR r.scope IN $active_scopes)
SET r.status = 'revoked',
    r.predicate = 'REVOKED',
    r.valid_until = $now,
    r.recorded_to = $now,
    r.description = NULL,
    r.embedding = NULL
RETURN count(r) AS revoked_count
"""

PURGE_USER_EVENTS_QUERY = """
MATCH (ev:Event {tenant_id: $tenant_id})
WHERE (ev.user_id = $user_id OR ev.agent_id = $user_id)
  AND ($is_system = true OR ev.scope IN $active_scopes)
DETACH DELETE ev
RETURN count(ev) AS deleted_events
"""

PURGE_USER_EPISODES_QUERY = """
MATCH (ep:Episode {tenant_id: $tenant_id})
WHERE (ep.user_id = $user_id OR ep.agent_id = $user_id)
  AND ($is_system = true OR ep.scope IN $active_scopes)
DETACH DELETE ep
RETURN count(ep) AS deleted_episodes
"""

ATOMIC_WRITE_CARDINALITY_ONE_QUERY = """
MERGE (s:Entity {norm_name: $s_norm_name, tenant_id: $tenant_id})
ON CREATE SET s.id = $s_id,
              s.name = $s_name,
              s.embedding = $s_emb,
              s.embedding_pending = $s_pending,
              s.created_at = $now,
              s.updated_at = $now
ON MATCH SET s.name = coalesce(s.name, $s_name),
             s.embedding = coalesce(s.embedding, $s_emb),
             s.updated_at = $now
SET s._write_lock = true

MERGE (o:Entity {norm_name: $o_norm_name, tenant_id: $tenant_id})
ON CREATE SET o.id = $o_id,
              o.name = $o_name,
              o.embedding = $o_emb,
              o.embedding_pending = $o_pending,
              o.created_at = $now,
              o.updated_at = $now
ON MATCH SET o.name = coalesce(o.name, $o_name),
             o.embedding = coalesce(o.embedding, $o_emb),
             o.updated_at = $now

WITH s, o
OPTIONAL MATCH (s)-[existing:FACT {predicate: $predicate, tenant_id: $tenant_id, scope: $scope, status: 'active'}]->(old_o:Entity)

WITH s, o, existing, old_o,
     (existing IS NOT NULL AND old_o.norm_name = $o_norm_name) AS is_duplicate,
     (existing IS NOT NULL AND old_o.norm_name <> $o_norm_name AND ($confidence >= existing.confidence OR $is_explicit_mutation = true)) AS should_supersede,
     (existing IS NOT NULL AND old_o.norm_name <> $o_norm_name AND $confidence < existing.confidence AND $is_explicit_mutation = false) AS should_conflict

FOREACH (_ IN CASE WHEN should_supersede THEN [1] ELSE [] END |
    SET existing.status = 'superseded',
        existing.valid_until = $now,
        existing.recorded_to = $now,
        existing.superseded_by = $fact_id
)

FOREACH (_ IN CASE WHEN is_duplicate THEN [] ELSE [1] END |
    CREATE (s)-[r:FACT {
        id: $fact_id,
        idempotency_key: $idempotency_key,
        predicate: $predicate,
        description: $description,
        embedding: $rel_emb,
        embedding_pending: $rel_pending,
        scope: $scope,
        tenant_id: $tenant_id,
        status: CASE WHEN should_conflict THEN 'conflicted' ELSE 'active' END,
        version: CASE WHEN existing IS NOT NULL THEN coalesce(existing.version, 1) + 1 ELSE $version END,
        previous_version_id: CASE WHEN should_supersede THEN existing.id ELSE NULL END,
        confidence: $confidence,
        trust_weight: $trust_weight,
        valid_from: $valid_from,
        valid_until: $valid_until,
        recorded_from: $now,
        recorded_to: NULL,
        source_agent_id: $source_agent_id,
        dataset_id: $dataset_id,
        created_at: $now
    }]->(o)
)

REMOVE s._write_lock

WITH s, o, existing, is_duplicate, should_conflict
OPTIONAL MATCH (s)-[res_r:FACT {predicate: $predicate, tenant_id: $tenant_id, scope: $scope}]->(o)
WHERE (is_duplicate AND res_r.id = existing.id) OR (NOT is_duplicate AND res_r.id = $fact_id)
RETURN s, res_r AS r, o, is_duplicate, should_conflict
"""

GRAPH_SNAPSHOT_NODES_QUERY = """
MATCH (n:Entity)
WHERE ($tenant_id IS NULL OR n.tenant_id = $tenant_id)
RETURN n.id AS id, n.name AS name, n.kind AS kind, n.tenant_id AS tenant_id,
       n.embedding IS NOT NULL AS has_embedding
LIMIT 500
"""

GRAPH_SNAPSHOT_EDGES_QUERY = """
MATCH (s:Entity)-[r:FACT]->(t:Entity)
WHERE {filter_clause}
RETURN r.id AS id, s.id AS source, t.id AS target, r.predicate AS predicate,
       r.description AS description, r.scope AS scope, r.confidence AS confidence,
       r.status AS status, r.valid_from AS valid_from, r.valid_until AS valid_until
LIMIT 1000
"""

SEARCH_ENTITIES_PREFIX_QUERY = """
MATCH (e:Entity {tenant_id: $tenant_id})
WHERE $prefix = '' OR toLower(e.name) STARTS WITH toLower($prefix)
RETURN e.id AS id, e.name AS name, e.tenant_id AS tenant_id, e.created_at AS created_at, e.embedding AS embedding
LIMIT $limit
"""

ADD_ENTITY_QUERY = """
MERGE (e:Entity {name: $name, tenant_id: $tenant_id})
ON CREATE SET e.id = $id,
              e.embedding = $embedding,
              e.created_at = $now,
              e.updated_at = $now
RETURN e.id AS id, e.name AS name, e.tenant_id AS tenant_id, e.created_at AS created_at, e.embedding AS embedding
"""

RETROSPECTIVE_FACTS_QUERY = """
MATCH (s:Entity)-[e:FACT]-(t:Entity)
WHERE (s.id IN $seed_ids OR s.name IN $seed_ids OR s.name IN $seed_names OR
       t.id IN $seed_ids OR t.name IN $seed_ids OR t.name IN $seed_names)
  AND e.tenant_id = $tenant_id
  AND e.status IN ['active', 'superseded']
  AND ($active_scopes IS NULL OR e.scope IN $active_scopes)
RETURN startNode(e).id AS s_id, startNode(e).name AS s_name,
       endNode(e).id AS t_id, endNode(e).name AS t_name,
       e.id AS edge_id, e.predicate AS predicate,
       e.description AS description, e.confidence AS confidence,
       e.trust_weight AS trust_weight, e.scope AS scope,
       e.status AS status, e.valid_from AS valid_from, e.valid_until AS valid_until,
       e.created_at AS created_at
ORDER BY (CASE WHEN e.status = 'superseded' THEN 1 ELSE 0 END) DESC, e.created_at DESC
LIMIT $limit
"""

TRAVERSAL_1HOP_QUERY = """
MATCH (s:Entity)-[e:FACT]-(t:Entity)
WHERE (s.id IN $seed_ids OR s.name IN $seed_ids OR t.id IN $seed_ids OR t.name IN $seed_ids)
  AND {filter_1hop}
RETURN startNode(e).id AS s_id, startNode(e).name AS s_name,
       endNode(e).id AS t_id, endNode(e).name AS t_name,
       e.id AS edge_id, e.predicate AS predicate,
       e.description AS description, e.confidence AS confidence,
       e.trust_weight AS trust_weight, e.scope AS scope,
       e.created_at AS created_at
ORDER BY e.confidence DESC, e.created_at DESC
LIMIT $limit_1hop
"""

TRAVERSAL_2HOP_EXPANSION_QUERY = """
MATCH (s:Entity)-[e1:FACT]-(mid:Entity)-[e2:FACT]-(t:Entity)
WHERE (s.id IN $seed_ids OR s.name IN $seed_ids)
  AND s.id <> t.id AND s.id <> mid.id AND mid.id <> t.id
  AND s <> t AND s <> mid AND mid <> t AND e1 <> e2
  AND {filter_e1}
  AND {filter_e2}
  AND size([(mid)-[:FACT]-() | 1]) <= $max_hub_degree
RETURN s.id AS s_id, s.name AS s_name,
       mid.id AS mid_id, mid.name AS mid_name,
       t.id AS t_id, t.name AS t_name,
       startNode(e1).name AS e1_src, endNode(e1).name AS e1_dst,
       e1.id AS e1_id, e1.predicate AS e1_pred, e1.confidence AS e1_conf,
       e1.scope AS e1_scope, e1.created_at AS e1_created, e1.description AS e1_desc,
       startNode(e2).name AS e2_src, endNode(e2).name AS e2_dst,
       e2.id AS e2_id, e2.predicate AS e2_pred, e2.confidence AS e2_conf,
       e2.scope AS e2_scope, e2.created_at AS e2_created, e2.description AS e2_desc
ORDER BY (coalesce(e1.confidence, 1.0) + coalesce(e2.confidence, 1.0)) DESC
LIMIT $limit_2hop
"""

HYBRID_VECTOR_ENTITY_QUERY = """
CALL db.index.vector.queryNodes('entity_embedding_idx', $top_k, $query_vec)
YIELD node, score
WHERE node.tenant_id = $tenant_id
  AND ($active_scopes IS NULL OR EXISTS {
      MATCH (node)-[r:FACT]-(:Entity)
      WHERE r.scope IN $active_scopes
  })
RETURN node.id AS id, node.name AS name, score AS vector_score
"""

HYBRID_VECTOR_ENTITY_ALT_QUERY = """
CALL db.index.vector.queryNodes('entity_vector_index', $top_k, $query_vec)
YIELD node, score
WHERE node.tenant_id = $tenant_id
  AND ($active_scopes IS NULL OR EXISTS {
      MATCH (node)-[r:FACT]-(:Entity)
      WHERE r.scope IN $active_scopes
  })
RETURN node.id AS id, node.name AS name, score AS vector_score
"""

HYBRID_FULLTEXT_ENTITY_QUERY = """
CALL db.index.fulltext.queryNodes('entity_name_ft', $lucene_query)
YIELD node, score
WHERE node.tenant_id = $tenant_id
  AND ($active_scopes IS NULL OR EXISTS {
      MATCH (node)-[r:FACT]-(:Entity)
      WHERE r.scope IN $active_scopes
  })
RETURN node.id AS id, node.name AS name, score AS bm25_score
LIMIT $top_k
"""

SEMANTIC_SEED_FALLBACK_ENTITY_QUERY = """
MATCH (e:Entity)-[r:FACT]-(:Entity)
WHERE e.tenant_id = $tenant_id
  AND ($active_scopes IS NULL OR r.scope IN $active_scopes)
  AND e.embedding IS NOT NULL
WITH DISTINCT e, vector.similarity.cosine(e.embedding, $query_vec) AS score
ORDER BY score DESC
RETURN e.id AS id, e.name AS name, score
LIMIT $top_k
"""

SEMANTIC_SEED_FALLBACK_FACT_QUERY = """
MATCH (s:Entity)-[r:FACT]->(o:Entity)
WHERE r.tenant_id = $tenant_id
  AND ($active_scopes IS NULL OR r.scope IN $active_scopes)
  AND r.embedding IS NOT NULL
WITH s, o, vector.similarity.cosine(r.embedding, $query_vec) AS score
ORDER BY score DESC
UNWIND [s, o] AS node
RETURN DISTINCT node.id AS id, node.name AS name, max(score) AS score
ORDER BY score DESC
LIMIT $top_k
"""

FIND_PENDING_EDGE_EMBEDDINGS_QUERY = """
MATCH ()-[r:FACT]->()
WHERE r.embedding_pending = true
RETURN r.id AS id, r.predicate AS predicate, r.description AS description
LIMIT $limit
"""

UPDATE_EDGE_EMBEDDING_QUERY = """
MATCH ()-[r:FACT {id: $id}]->()
SET r.embedding = $vector,
    r.embedding_pending = false
"""

FIND_PENDING_ENTITY_EMBEDDINGS_QUERY = """
MATCH (n:Entity)
WHERE n.embedding_pending = true
RETURN n.id AS id, n.name AS name
LIMIT $limit
"""

UPDATE_ENTITY_EMBEDDING_QUERY = """
MATCH (n:Entity {id: $id})
SET n.embedding = $vector,
    n.embedding_pending = false
"""

