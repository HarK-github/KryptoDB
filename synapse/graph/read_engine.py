from synapse.storage.neo4j_client import neo4j_client
from synapse.daemon.keyring import keyring
from synapse.integrity.partition_crypto import Encryptor
from datetime import datetime, timezone
import json

encryptor = Encryptor()

class ReadEngine:
    def __init__(self):
        pass

    def retrieve_active_facts(self, allowed_scopes: list[str], subject_id: str = None, query_str: str = None) -> list[dict]:
        """
        Implements the 4-Gate Retrieval Path.
        1. Permission Gate (allowed_scopes provided by Config)
        2. Activation Gate (keyring._keys)
        3. Temporal Gate (valid_to IS NULL)
        4. Decryption
        """
        # 1 & 2: Intersect Permission and Activation Gates
        active_partitions = list(keyring._keys.keys())
        effective_scopes = list(set(allowed_scopes) & set(active_partitions))
        
        if not effective_scopes:
            return []
            
        # 3. Neo4j Query + Temporal Gate + Trust Ranking
        if subject_id:
            subject_blind_indexes = [encryptor.compute_blind_idx_session(scope, subject_id) for scope in effective_scopes]
            subject_blind_indexes = [idx for idx in subject_blind_indexes if idx is not None]
            if not subject_blind_indexes:
                return []
                
            query = """
            MATCH path = (s)-[:SAME_AS*0..1]-(s_sim)-[*1..3]-(o)
            WHERE s.name_blind_idx IN $subject_blind_indexes AND ALL(rel in relationships(path) WHERE type(rel) = 'SAME_AS' OR (rel.permission_scope IN $scopes AND rel.valid_to IS NULL AND rel.revoked_at IS NULL))
            UNWIND relationships(path) as r
            WITH r WHERE type(r) <> 'SAME_AS'
            RETURN DISTINCT startNode(r) as s, r, endNode(r) as o
            ORDER BY r.trust_weight DESC
            """
            params = {"scopes": effective_scopes, "subject_blind_indexes": subject_blind_indexes}
            
        elif query_str:
            # Phase 10: Two-Stage Retrieval
            # Stage 1: Fast vector recall on non-sensitive metadata (tags_embedding)
            # Stage 2: Structural/Access filter on candidate set
            from synapse.integrity.embeddings import embedding_service
            query_emb = embedding_service.encode(query_str)
            
            query = """
            MATCH (n:Entity) 
            WHERE n.tags_embedding IS NOT NULL
            WITH n, gds.similarity.cosine(n.tags_embedding, $query_emb) AS sim
            WHERE sim > 0.3
            ORDER BY sim DESC LIMIT 50
            WITH collect(n.id) as candidate_ids
            
            MATCH (s)-[r:FACT]->(o)
            WHERE (s.id IN candidate_ids OR o.id IN candidate_ids)
              AND r.permission_scope IN $scopes
              AND r.valid_to IS NULL AND r.revoked_at IS NULL
            RETURN s, r, o
            ORDER BY r.trust_weight DESC
            LIMIT 10
            """
            params = {"scopes": effective_scopes, "query_emb": query_emb}
            
        else:
            # Fallback if args missing
            query = """
            MATCH (s)-[r:FACT]->(o)
            WHERE r.permission_scope IN $scopes
              AND r.valid_to IS NULL
              AND r.revoked_at IS NULL
            RETURN s, r, o
            ORDER BY r.trust_weight DESC
            LIMIT 10
            """
            params = {"scopes": effective_scopes}
        
        import uuid
        request_id = str(uuid.uuid4())
        
        try:
            records, _, _ = neo4j_client.execute_query_with_retry(query, **params)
        except Exception:
            return []
            
        import asyncio
        from synapse.daemon.events import event_bus, GraphEvent
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
            
        from synapse.memory_api.session_search import session_index
        results = []
        for step_idx, record in enumerate(records):
            s_node = record["s"]
            o_node = record["o"]
            r_edge = record["r"]
            
            scope = r_edge["permission_scope"]
            
            if loop:
                try:
                    ev = GraphEvent(
                        event_type="traversal_step",
                        permission_scope=scope,
                        timestamp=datetime.now(timezone.utc),
                        edge_id=r_edge.get("id"),
                        source_id=s_node.get("id"),
                        target_id=o_node.get("id"),
                        trust_weight=r_edge.get("trust_weight", 0.5),
                        request_id=request_id,
                        step_index=step_idx
                    )
                    loop.create_task(event_bus.publish(ev))
                except Exception:
                    pass
            
            
            # 4. Decrypt content fields using Encryptor
            s_name = encryptor.decrypt(scope, s_node.get("name_ct")) if s_node.get("name_ct") else None
            o_name = encryptor.decrypt(scope, o_node.get("name_ct")) if o_node.get("name_ct") else None
            
            edge_dict = {
                "edge_id": r_edge["id"],
                "subject": s_name or s_node.get("id"),
                "predicate": r_edge["predicate"],
                "object": o_name or o_node.get("id"),
                "trust_weight": r_edge["trust_weight"],
                "permission_scope": scope,
                "source_agent": r_edge["source_agent_id"]
            }
            results.append(edge_dict)
            
            # Milestone 8.4 Phase A: Index into transient Bloom Filter for future substring search
            session_index.add_edge(edge_dict)
            
        return results
