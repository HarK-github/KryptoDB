"""
Close-and-open supersession, lineage stamping.
"""
from datetime import datetime, timezone
from kryptodb.graph import Edge

import logging
import json

logger = logging.getLogger("kryptodb.trace")

class WriteEngine:
    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
        
    def write_fact(self, edge: Edge, existing_edge: Edge | None = None, trace_id: str | None = None) -> tuple[Edge, dict | None]:
        now = self._now()
        edge.valid_from = now
        edge.valid_to = None
        edge.ingested_at = now
        cascade_args = None
        
        if existing_edge:
            existing_edge.valid_to = now
            existing_edge.superseded_by = edge.id
            
        from kryptodb.integrity.partition_crypto import Encryptor
        from kryptodb.daemon.sessions import keyring
        encryptor = Encryptor()
        
        has_key = (
            edge.permission_scope in encryptor.scope_keys or
            keyring.get_key(edge.permission_scope) is not None or
            (not encryptor.customer_keys_required)
        )
        if not has_key:
            raise RuntimeError(f"Customer scope key is not configured for: {edge.permission_scope}.")
        
        knowledge_envelope = {
            "subject": edge.subject_id,
            "predicate": edge.predicate if hasattr(edge, "predicate") else edge.type,
            "description": getattr(edge, "description", None),
            "object": edge.object_id,
            "source_agent_id": edge.source_agent_id,
            "subject_tags": list(getattr(edge, "subject_tags", []) or []),
            "object_tags": list(getattr(edge, "object_tags", []) or []),
            "derived_from": list(edge.derived_from or []),
            "is_suspicious": edge.is_suspicious,
            "dataset_id": getattr(edge, "dataset_id", None),
        }
        
        subject_blind_idx = encryptor.compute_blind_idx(edge.permission_scope, edge.subject_id)
        object_blind_idx = encryptor.compute_blind_idx(edge.permission_scope, edge.object_id)
        knowledge_crypto = encryptor.encrypt_knowledge(edge.permission_scope, edge.id, knowledge_envelope)
        pending_envelope_str = None

        logger.info(json.dumps({
            "event": "stage_transition",
            "path": "write",
            "stage": "encrypt",
            "trace_id": trace_id,
            "fact_id": edge.id,
            "scope": edge.permission_scope,
            "status": "completed"
        }))
        
        from kryptodb.storage.neo4j_client import neo4j_client
        if neo4j_client.driver:
            # Execute Cypher
            query = """
            MERGE (s:Entity {name_blind_idx: $subject_blind_idx})
            ON CREATE SET s.id = randomUUID(), s.permission_scope = $permission_scope
            MERGE (o:Entity {name_blind_idx: $object_blind_idx})
            ON CREATE SET o.id = randomUUID(), o.permission_scope = $permission_scope
            
            CREATE (s)-[r:FACT {
                id: $edge_id,
                knowledge_id: $edge_id,
                encryption_status: $encryption_status,
                pending_envelope: $pending_envelope,
                knowledge_ct: $knowledge_ct,
                knowledge_nonce: $knowledge_nonce,
                wrapped_dek: $wrapped_dek,
                knowledge_kek_version: $knowledge_kek_version,
                subject_search_idx: $subject_search_idx,
                predicate_search_idx: $predicate_search_idx,
                object_search_idx: $object_search_idx,
                search_indexes: $search_indexes,
                permission_scope: $permission_scope,
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
                "edge_id": edge.id,
                "encryption_status": "encrypted",
                "pending_envelope": None,
                "knowledge_ct": knowledge_crypto["knowledge_ct"],
                "knowledge_nonce": knowledge_crypto["knowledge_nonce"],
                "wrapped_dek": knowledge_crypto["wrapped_dek"],
                "knowledge_kek_version": knowledge_crypto["knowledge_kek_version"],
                "subject_search_idx": knowledge_crypto["subject_search_idx"],
                "predicate_search_idx": knowledge_crypto["predicate_search_idx"],
                "object_search_idx": knowledge_crypto["object_search_idx"],
                "search_indexes": knowledge_crypto["search_indexes"],
                "permission_scope": edge.permission_scope,
                "trust_weight": edge.trust_weight if hasattr(edge, 'trust_weight') else 0.5,
                "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
                "ingested_at": edge.ingested_at.isoformat() if edge.ingested_at else None,
                "derived_from": list(edge.derived_from) if edge.derived_from else [],
                "is_suspicious": edge.is_suspicious
            }
            
            def _write_and_supersede_tx(tx):
                res = tx.run(query, params)
                record = res.single()
                sub_id = record["s_id"] if record else None
                obj_id = record["o_id"] if record else None
                
                if existing_edge:
                    update_query = """
                    MATCH ()-[r:FACT {id: $old_id}]->()
                    SET r.valid_to = $valid_to,
                        r.superseded_by = $superseded_by,
                        r.revoked_at = $valid_to
                    """
                    tx.run(
                        update_query, 
                        old_id=existing_edge.id,
                        valid_to=now.isoformat(),
                        superseded_by=edge.id
                    )
                return sub_id, obj_id
            
            if hasattr(neo4j_client, "execute_write_transaction"):
                try:
                    s_id, o_id = neo4j_client.execute_write_transaction(_write_and_supersede_tx, trace_id=trace_id)
                except TypeError:
                    s_id, o_id = neo4j_client.execute_write_transaction(_write_and_supersede_tx)
            else:
                try:
                    records, _, _ = neo4j_client.execute_query_with_retry(query, trace_id=trace_id, **params)
                except TypeError:
                    records, _, _ = neo4j_client.execute_query_with_retry(query, **params)
                if records:
                    s_id = records[0].get("s_id")
                    o_id = records[0].get("o_id")
                else:
                    s_id, o_id = None, None

            logger.info(json.dumps({
                "event": "stage_transition",
                "path": "write",
                "stage": "commit",
                "trace_id": trace_id,
                "fact_id": edge.id,
                "status": "committed"
            }))
            
            import asyncio
            from kryptodb.daemon import event_bus, GraphEvent
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
        from kryptodb.storage.neo4j_client import neo4j_client
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
            from kryptodb.daemon import event_bus, GraphEvent
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
