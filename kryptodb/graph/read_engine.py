from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.daemon.sessions import keyring
from kryptodb.integrity.partition_crypto import Encryptor
from datetime import datetime, timezone
import json
import logging

logger = logging.getLogger("kryptodb.trace")
encryptor = Encryptor()

class ReadEngine:
    def __init__(self, session_keyring=None):
        self.session_keyring = session_keyring

    def retrieve_active_facts(self, allowed_scopes: list[str], subject_id: str = None, query_str: str = None, query: str = None, trace_id: str | None = None) -> list[dict]:
        """
        Implements the 4-Gate Retrieval Path.
        1. Permission Gate (allowed_scopes provided by Config)
        2. Activation Gate (keyring._keys)
        3. Temporal Gate (valid_to IS NULL)
        4. Decryption
        """
        if query_str is None:
            query_str = query

        # 1 & 2: Intersect Permission and Activation Gates
        sk = self.session_keyring or keyring
        sk_keys = getattr(sk, "_keys", getattr(sk, "keys", {}))
        active_partitions = list(sk_keys.keys()) if isinstance(sk_keys, dict) else list(sk_keys)
        if not active_partitions and keyring._keys:
            active_partitions = list(keyring._keys.keys())

        effective_scopes = list(set(allowed_scopes) & set(active_partitions))
        if not effective_scopes:
            return []
            
        # 3. Neo4j Query + Temporal Gate + Trust Ranking
        if subject_id:
            subject_search_indexes = [encryptor.compute_search_idx_session(scope, "subject", subject_id, self.session_keyring) for scope in effective_scopes]
            subject_search_indexes = [idx for idx in subject_search_indexes if idx is not None]
            legacy_blind_indexes = [encryptor.compute_blind_idx_session(scope, subject_id, self.session_keyring) for scope in effective_scopes]
            legacy_blind_indexes = [idx for idx in legacy_blind_indexes if idx is not None]
            
            if not subject_search_indexes and not legacy_blind_indexes:
                return []
                
            query = """
            MATCH (s)-[r:FACT]->(o)
            WHERE (r.subject_search_idx IN $subject_search_indexes OR s.name_blind_idx IN $legacy_blind_indexes)
              AND r.permission_scope IN $scopes
              AND r.valid_to IS NULL AND r.revoked_at IS NULL
            RETURN s, r, o
            ORDER BY r.trust_weight DESC
            """
            params = {
                "scopes": effective_scopes,
                "subject_search_indexes": subject_search_indexes,
                "legacy_blind_indexes": legacy_blind_indexes,
            }
            
        elif query_str:
            # Encrypted token search. Query terms are converted to HMAC tokens
            # while the session key is active; Neo4j never sees the plaintext.
            query_terms = {" ".join(query_str.casefold().split())}
            query_terms.update(term for term in query_str.casefold().split() if term)
            query_indexes = []
            for scope in effective_scopes:
                for field in ("subject", "predicate", "object"):
                    for term in query_terms:
                        token = encryptor.compute_search_idx_session(scope, field, term, self.session_keyring)
                        if token:
                            query_indexes.append(token)
                            
            if not query_indexes:
                # Without encrypted search tokens there is no safe way
                # to establish relevance. Returning arbitrary facts here
                # would contaminate the model's context.
                return []

            query = """
            MATCH (s)-[r:FACT]->(o)
            WHERE ANY(idx IN coalesce(r.search_indexes, []) WHERE idx IN $query_indexes)
              AND r.permission_scope IN $scopes
              AND r.valid_to IS NULL AND r.revoked_at IS NULL
            RETURN s, r, o
            ORDER BY r.trust_weight DESC
            LIMIT 10
            """
            params = {"scopes": effective_scopes, "query_indexes": query_indexes}
            
        else:
            # Fallback if args missing
            query = """
            MATCH (s)-[r:FACT]->(o)
            WHERE r.permission_scope IN $scopes
              AND r.valid_to IS NULL AND r.revoked_at IS NULL
            RETURN s, r, o
            ORDER BY r.trust_weight DESC
            LIMIT 10
            """
            params = {"scopes": effective_scopes}

        import uuid
        request_id = str(uuid.uuid4())
        
        try:
            if hasattr(neo4j_client, "execute_query_with_retry"):
                try:
                    records, _, _ = neo4j_client.execute_query_with_retry(query, trace_id=trace_id, **params)
                except TypeError:
                    records, _, _ = neo4j_client.execute_query_with_retry(query, **params)
            else:
                records, _, _ = neo4j_client.driver.execute_query(query, **params)

            logger.info(json.dumps({
                "event": "gate_transition",
                "path": "read",
                "gate": "blind_index_match",
                "trace_id": trace_id,
                "status": "passed" if records else "empty",
                "matched_records": len(records),
                "effective_scopes": effective_scopes
            }))
        except Exception as exc:
            logger.debug("Error in retrieve_active_facts: %s", exc)
            return []
            
        import asyncio
        from kryptodb.daemon import event_bus, GraphEvent
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
            
        results = []
        traversal_path = []
        for step_idx, record in enumerate(records):
            s_node = record["s"]
            o_node = record["o"]
            r_edge = record["r"]
            
            scope = r_edge["permission_scope"]
            
            traversal_path.append({
                "edgeId": str(r_edge.get("id")),
                "sourceId": str(s_node.get("id")),
                "targetId": str(o_node.get("id")),
            })
            
            
            # 4. Decrypt content fields using Encryptor
            knowledge = None
            if r_edge.get("knowledge_ct") and r_edge.get("wrapped_dek"):
                knowledge = encryptor.decrypt_knowledge(
                    scope,
                    r_edge.get("knowledge_id") or r_edge.get("id"),
                    dict(r_edge),
                    self.session_keyring,
                )
                
            if knowledge:
                s_name = knowledge.get("subject")
                o_name = knowledge.get("object")
            elif s_node.get("name_ct"):
                try:
                    s_name = encryptor.decrypt(scope, s_node.get("name_ct"), self.session_keyring)
                except TypeError:
                    s_name = encryptor.decrypt(scope, s_node.get("name_ct"))
                try:
                    o_name = encryptor.decrypt(scope, o_node.get("name_ct"), self.session_keyring) if o_node.get("name_ct") else None
                except TypeError:
                    o_name = encryptor.decrypt(scope, o_node.get("name_ct")) if o_node.get("name_ct") else None
            else:
                s_name = None
                o_name = None

            predicate = knowledge.get("predicate") if knowledge else r_edge.get("predicate")
            
            edge_dict = {
                "edge_id": r_edge["id"],
                "subject": s_name or s_node.get("id"),
                "predicate": predicate,
                "description": knowledge.get("description") if knowledge else None,
                "object": o_name or o_node.get("id"),
                "trust_weight": r_edge["trust_weight"],
                "permission_scope": scope,
                "source_agent": knowledge.get("source_agent_id") if knowledge else r_edge.get("source_agent_id")
            }
            if knowledge and knowledge.get("dataset_id"):
                edge_dict["dataset_id"] = knowledge["dataset_id"]
                
            results.append(edge_dict)

        logger.info(json.dumps({
            "event": "gate_transition",
            "path": "read",
            "gate": "decrypt",
            "trace_id": trace_id,
            "status": "passed",
            "decrypted_count": len(results)
        }))

        # Do not expose partial query results as traversal events. The graph
        # client receives one ordered path only after the full query has been
        # read and decrypted.
        if loop and traversal_path:
            try:
                loop.create_task(event_bus.publish(GraphEvent(
                    event_type="traversal_complete",
                    permission_scope=str(traversal_path and records[0]["r"].get("permission_scope", "research")),
                    timestamp=datetime.now(timezone.utc),
                    request_id=request_id,
                    traversal_path=traversal_path,
                )))
            except Exception:
                pass
            
        return results
