"""Encrypted Cypher queries and Neo4j 5.21 Community schema DDL (v3 spec).

Store invariant: ZERO plaintext knowledge fields (name, predicate, object,
description, content, summary, embedding) are ever written to Neo4j.
"""

from __future__ import annotations

ENCRYPTED_SCHEMA_DDL = [
    """
    CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
    FOR (n:Entity) REQUIRE n.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT idempotency_scope_key_unique IF NOT EXISTS
    FOR (k:IdempotencyKey) REQUIRE (k.scope, k.key) IS UNIQUE
    """,
    """
    CREATE INDEX entity_scope_bidx_idx IF NOT EXISTS
    FOR (n:Entity) ON (n.scope, n.name_bidx)
    """,
    """
    CREATE INDEX fact_scope_status_idx IF NOT EXISTS
    FOR ()-[r:FACT]-() ON (r.scope, r.status)
    """,
    """
    CREATE INDEX fact_subject_pred_bidx_idx IF NOT EXISTS
    FOR ()-[r:FACT]-() ON (r.scope, r.subject_bidx, r.predicate_bidx)
    """,
]

FIND_ENTITIES_BY_BIDX_QUERY = """
MATCH (n:Entity {scope: $scope, name_bidx: $name_bidx})
RETURN n.id AS id, n.scope AS scope, n.name_bidx AS name_bidx,
       n.name_ct AS name_ct, n.wrapped_dek AS wrapped_dek, n.kid AS kid
"""

CREATE_ENCRYPTED_ENTITY_QUERY = """
CREATE (n:Entity {
    id: $id,
    scope: $scope,
    name_bidx: $name_bidx,
    name_ct: $name_ct,
    wrapped_dek: $wrapped_dek,
    kid: $kid,
    created_at: $now,
    updated_at: $now
})
RETURN n.id AS id
"""

FIND_IDEMPOTENCY_KEY_QUERY = """
MATCH (k:IdempotencyKey {scope: $scope, key: $idempotency_key})
RETURN k.fact_id AS fact_id
LIMIT 1
"""

WRITE_ENCRYPTED_FACT_QUERY = """
MATCH (s:Entity {id: $subject_entity_id, scope: $scope})
MATCH (o:Entity {id: $object_entity_id, scope: $scope})
CREATE (k:IdempotencyKey {
    scope: $scope,
    key: $idempotency_key,
    fact_id: $fact_id,
    created_at: $now
})
CREATE (s)-[r:FACT {
    id: $fact_id,
    scope: $scope,
    status: $status,
    version: $version,
    valid_from: $valid_from,
    valid_until: $valid_until,
    recorded_from: $now,
    recorded_to: NULL,
    confidence: $confidence,
    trust_weight: $trust_weight,
    subject_bidx: $subject_bidx,
    predicate_bidx: $predicate_bidx,
    object_bidx: $object_bidx,
    search_tokens: $search_tokens,
    knowledge_ct: $knowledge_ct,
    wrapped_dek: $wrapped_dek,
    kid: $kid,
    created_at: $now
}]->(o)
RETURN r.id AS id
"""

GET_ENCRYPTED_FACT_QUERY = """
MATCH (s:Entity)-[r:FACT {id: $fact_id, scope: $scope}]->(o:Entity)
RETURN properties(r) AS props
LIMIT 1
"""

FIND_ACTIVE_ENCRYPTED_BY_BIDX_QUERY = """
MATCH (s:Entity)-[r:FACT {scope: $scope, subject_bidx: $subject_bidx, predicate_bidx: $predicate_bidx}]->(o:Entity)
WHERE r.status = 'active'
  AND r.valid_from <= $now
  AND (r.valid_until IS NULL OR r.valid_until > $now)
RETURN properties(r) AS props
ORDER BY r.version DESC, r.created_at DESC
"""

ATOMIC_SUPERSEDE_ENCRYPTED_FACT_QUERY = """
MATCH ()-[old_r:FACT {id: $old_id}]->()
SET old_r._lock = true
WITH old_r
WHERE old_r.scope = $scope
  AND old_r.status = 'active'
  AND old_r.version = $expected_version
SET old_r.status = 'superseded',
    old_r.valid_until = $now,
    old_r.recorded_to = $now,
    old_r.superseded_by = $fact_id,
    old_r.knowledge_ct = $closed_knowledge_ct,
    old_r.wrapped_dek = $closed_wrapped_dek,
    old_r.kid = $closed_kid
REMOVE old_r._lock

WITH old_r
MATCH (s:Entity {id: $subject_entity_id, scope: $scope})
MATCH (o:Entity {id: $object_entity_id, scope: $scope})
CREATE (k:IdempotencyKey {
    scope: $scope,
    key: $idempotency_key,
    fact_id: $fact_id,
    created_at: $now
})
CREATE (s)-[r:FACT {
    id: $fact_id,
    scope: $scope,
    status: 'active',
    version: $version,
    previous_version_id: $old_id,
    valid_from: $valid_from,
    valid_until: NULL,
    recorded_from: $now,
    recorded_to: NULL,
    confidence: $confidence,
    trust_weight: $trust_weight,
    subject_bidx: $subject_bidx,
    predicate_bidx: $predicate_bidx,
    object_bidx: $object_bidx,
    search_tokens: $search_tokens,
    knowledge_ct: $knowledge_ct,
    wrapped_dek: $wrapped_dek,
    kid: $kid,
    created_at: $now
}]->(o)
RETURN r.id AS id
"""

FORGET_ENCRYPTED_FACT_QUERY = """
MATCH ()-[r:FACT {id: $fact_id, scope: $scope}]->()
WHERE r.status = 'active'
SET r.status = 'revoked',
    r.valid_until = $now,
    r.recorded_to = $now,
    r.knowledge_ct = NULL,
    r.wrapped_dek = NULL,
    r.kid = NULL,
    r.subject_bidx = NULL,
    r.predicate_bidx = NULL,
    r.object_bidx = NULL,
    r.search_tokens = []
WITH r
OPTIONAL MATCH (k:IdempotencyKey {scope: $scope, fact_id: $fact_id})
DELETE k
RETURN r.id AS id
"""


LIST_ALL_ACTIVE_ENCRYPTED_FACTS_QUERY = """
MATCH (s:Entity)-[r:FACT {scope: $scope}]->(o:Entity)
WHERE r.status = 'active'
  AND r.valid_from <= $now
  AND (r.valid_until IS NULL OR r.valid_until > $now)
RETURN properties(r) AS props
"""

FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY = """
MATCH (s:Entity)-[r1:FACT {scope: $scope}]->(o:Entity)
WHERE r1.id IN $fact_ids
  AND r1.status = 'active'
  AND r1.valid_from <= $now
  AND (r1.valid_until IS NULL OR r1.valid_until > $now)
WITH collect(DISTINCT r1) AS hop1_edges, collect(DISTINCT s) + collect(DISTINCT o) AS seed_nodes
UNWIND hop1_edges AS e1
RETURN properties(e1) AS props, 1 AS hops
LIMIT $max_results
UNION
MATCH (s:Entity)-[r1:FACT {scope: $scope}]->(o:Entity)
WHERE r1.id IN $fact_ids AND r1.status = 'active'
WITH collect(DISTINCT s) + collect(DISTINCT o) AS seed_nodes, $fact_ids AS seed_ids
UNWIND seed_nodes AS seed
MATCH (seed)-[r2:FACT {scope: $scope}]-(nbr:Entity)
WHERE NOT r2.id IN seed_ids
  AND r2.status = 'active'
  AND r2.valid_from <= $now
  AND (r2.valid_until IS NULL OR r2.valid_until > $now)
  AND nbr <> seed
RETURN DISTINCT properties(r2) AS props, 2 AS hops
LIMIT $max_results
"""

SEARCH_ENCRYPTED_BY_TOKENS_QUERY = """
MATCH (s:Entity)-[r1:FACT {scope: $scope}]->(o:Entity)
WHERE r1.status = 'active'
  AND r1.valid_from <= $now
  AND (r1.valid_until IS NULL OR r1.valid_until > $now)
  AND (
      s.name_bidx IN $entity_bidxs
      OR o.name_bidx IN $entity_bidxs
      OR ANY(t IN coalesce(r1.search_tokens, []) WHERE t IN $word_tokens)
  )
RETURN DISTINCT properties(r1) AS props, 1 AS hops
LIMIT $max_results
UNION
MATCH (s:Entity)-[r1:FACT {scope: $scope}]->(o:Entity)
WHERE r1.status = 'active'
  AND (
      s.name_bidx IN $entity_bidxs
      OR o.name_bidx IN $entity_bidxs
      OR ANY(t IN coalesce(r1.search_tokens, []) WHERE t IN $word_tokens)
  )
WITH collect(DISTINCT r1.id) AS hop1_ids, collect(DISTINCT s) + collect(DISTINCT o) AS seed_nodes
UNWIND seed_nodes AS seed
MATCH (seed)-[r2:FACT {scope: $scope}]-(nbr:Entity)
WHERE NOT r2.id IN hop1_ids
  AND r2.status = 'active'
  AND r2.valid_from <= $now
  AND (r2.valid_until IS NULL OR r2.valid_until > $now)
  AND nbr <> seed
RETURN DISTINCT properties(r2) AS props, 2 AS hops
LIMIT $max_results
"""


AUDIT_ENTITY_PROPERTIES_QUERY = """
MATCH (n:Entity)
RETURN n.id AS id, keys(n) AS prop_keys,
       n.name_ct IS NOT NULL AS has_ct,
       n.wrapped_dek IS NOT NULL AS has_dek
"""

AUDIT_FACT_PROPERTIES_QUERY = """
MATCH ()-[r:FACT]->()
RETURN r.id AS id, r.status AS status, keys(r) AS prop_keys,
       r.knowledge_ct IS NOT NULL AS has_ct,
       r.wrapped_dek IS NOT NULL AS has_dek
"""

AUDIT_EVENT_PROPERTIES_QUERY = """
MATCH (ev:Event)
RETURN ev.id AS id, keys(ev) AS prop_keys,
       ev.content_ct IS NOT NULL AS has_ct,
       ev.wrapped_dek IS NOT NULL AS has_dek
"""

AUDIT_EPISODE_PROPERTIES_QUERY = """
MATCH (ep:Episode)
RETURN ep.id AS id, keys(ep) AS prop_keys,
       ep.summary_ct IS NOT NULL AS has_ct,
       ep.wrapped_dek IS NOT NULL AS has_dek
"""
