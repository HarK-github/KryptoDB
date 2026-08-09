"""
Close-and-open supersession, lineage stamping.
"""
from datetime import datetime, timezone
from synapse.graph.schema import Edge

class WriteEngine:
    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
        
    def write_fact(self, edge: Edge, existing_edge: Edge | None = None) -> tuple[Edge, dict | None]:
        now = self._now()
        edge.valid_from = now
        edge.valid_to = None
        edge.ingested_at = now
        cascade_args = None
        
        if existing_edge:
            existing_edge.valid_to = now
            existing_edge.superseded_by = edge.id
            
        from synapse.integrity.partition_crypto import Encryptor
        from synapse.integrity.embeddings import embedding_service
        encryptor = Encryptor()
        subject_blind_idx = encryptor.compute_blind_idx(edge.permission_scope, edge.subject_id)
        object_blind_idx = encryptor.compute_blind_idx(edge.permission_scope, edge.object_id)
        
        subject_ct = encryptor.encrypt(edge.permission_scope, edge.subject_id)
        object_ct = encryptor.encrypt(edge.permission_scope, edge.object_id)
        
        subject_tags_emb = embedding_service.encode(" ".join(edge.subject_tags)) if getattr(edge, 'subject_tags', None) else None
        object_tags_emb = embedding_service.encode(" ".join(edge.object_tags)) if getattr(edge, 'object_tags', None) else None
        
        from synapse.storage.neo4j_client import neo4j_client
        if neo4j_client.driver:
            # Execute Cypher
            query = """
            MERGE (s:Entity {name_blind_idx: $subject_blind_idx})
            ON CREATE SET s.id = randomUUID(), s.permission_scope = $permission_scope, s.name_ct = $subject_ct, s.tags_embedding = $subject_tags_emb
            MERGE (o:Entity {name_blind_idx: $object_blind_idx})
            ON CREATE SET o.id = randomUUID(), o.permission_scope = $permission_scope, o.name_ct = $object_ct, o.tags_embedding = $object_tags_emb
            
            CREATE (s)-[r:FACT {
                id: $edge_id,
                predicate: $predicate,
                permission_scope: $permission_scope,
                source_agent_id: $source_agent_id,
                trust_weight: $trust_weight,
                valid_from: $valid_from,
                ingested_at: $ingested_at,
                derived_from: $derived_from,
                is_suspicious: $is_suspicious
            }]->(o)
            RETURN s.id AS s_id, o.id AS o_id
            """
            params = {
                "subject_blind_idx": subject_blind_idx,
                "object_blind_idx": object_blind_idx,
                "subject_ct": subject_ct,
                "object_ct": object_ct,
                "subject_tags_emb": subject_tags_emb,
                "object_tags_emb": object_tags_emb,
                "edge_id": edge.id,
                "predicate": edge.predicate if hasattr(edge, 'predicate') else edge.type,
                "permission_scope": edge.permission_scope,
                "source_agent_id": edge.source_agent_id,
                "trust_weight": edge.trust_weight if hasattr(edge, 'trust_weight') else 0.5,
                "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
                "ingested_at": edge.ingested_at.isoformat() if edge.ingested_at else None,
                "derived_from": list(edge.derived_from) if edge.derived_from else [],
                "is_suspicious": edge.is_suspicious
            }
            records, _, _ = neo4j_client.execute_query_with_retry(query, **params)
            
            s_id = records[0]["s_id"] if records else None
            o_id = records[0]["o_id"] if records else None
            
            import asyncio
            from synapse.daemon.events import event_bus, GraphEvent
            try:
                loop = asyncio.get_running_loop()
                ev = GraphEvent(
                    event_type="edge_created",
                    permission_scope=edge.permission_scope,
                    timestamp=now,
                    edge_id=edge.id,
                    source_id=s_id,
                    target_id=o_id,
                    trust_weight=params["trust_weight"]
                )
                loop.create_task(event_bus.publish(ev))
            except Exception:
                pass
            
            if existing_edge:
                update_query = """
                MATCH ()-[r:FACT {id: $old_id}]->()
                SET r.valid_to = $valid_to,
                    r.superseded_by = $superseded_by,
                    r.revoked_at = $valid_to
                """
                neo4j_client.execute_query_with_retry(
                    update_query, 
                    old_id=existing_edge.id,
                    valid_to=now.isoformat(),
                    superseded_by=edge.id
                )
                cascade_args = {
                    "revoked_edge_id": existing_edge.id,
                    "valid_to_iso": now.isoformat(),
                    "superseded_by_id": edge.id
                }
                try:
                    loop = asyncio.get_running_loop()
                    ev_close = GraphEvent(
                        event_type="edge_closed",
                        permission_scope=existing_edge.permission_scope,
                        timestamp=now,
                        edge_id=existing_edge.id
                    )
                    loop.create_task(event_bus.publish(ev_close))
                except Exception:
                    pass

        return edge, cascade_args
        
    def _cascade_revocation(self, revoked_edge_id: str, valid_to_iso: str, superseded_by_id: str):
        from synapse.storage.neo4j_client import neo4j_client
        if not neo4j_client.driver:
            return
            
        query = """
        MATCH ()-[r:FACT]->()
        WHERE $revoked_id IN r.derived_from AND r.valid_to IS NULL
        RETURN r.id AS child_id, r.permission_scope AS permission_scope
        """
        records, _, _ = neo4j_client.execute_query_with_retry(query, revoked_id=revoked_edge_id)
        
        for record in records:
            child_id = record["child_id"]
            child_scope = record["permission_scope"]
            
            close_q = """
            MATCH ()-[r:FACT {id: $child_id}]->()
            SET r.valid_to = $valid_to,
                r.superseded_by = $superseded_by
            """
            neo4j_client.execute_query_with_retry(
                close_q, 
                child_id=child_id, 
                valid_to=valid_to_iso, 
                superseded_by=f"CASCADE:{superseded_by_id}"
            )
            
            import asyncio
            from synapse.daemon.events import event_bus, GraphEvent
            try:
                loop = asyncio.get_running_loop()
                ev = GraphEvent(
                    event_type="cascade_step",
                    permission_scope=child_scope,
                    timestamp=self._now(),
                    edge_id=child_id
                )
                loop.create_task(event_bus.publish(ev))
            except Exception:
                pass
            
            # Recurse down the tree
            self._cascade_revocation(child_id, valid_to_iso, superseded_by_id)
        
    def assert_lineage(self, edge: Edge, is_derived: bool):
        if is_derived and not edge.derived_from:
            raise ValueError("Derived fact must have derived_from lineage")
        if not is_derived and edge.derived_from:
            raise ValueError("Primary fact cannot have derived_from lineage")
