from kryptodb.storage.neo4j_client import neo4j_client
from kryptodb.daemon.keyring import keyring
from kryptodb.integrity.partition_crypto import Encryptor
from kryptodb.integrity.embeddings import embedding_service
from datetime import datetime, timezone
import json

encryptor = Encryptor()

class ReadEngine:
    def __init__(self, session_keyring=None, session_search_index=None):
        self.session_keyring = session_keyring
        self.session_search_index = session_search_index

    def retrieve_active_facts(self, allowed_scopes: list[str], subject_id: str = None, query_str: str = None, query: str = None) -> list[dict]:
        """
        Implements the 4-Gate Retrieval Path.
        1. Permission Gate (allowed_scopes provided by Config)
        2. Activation Gate (keyring._keys)
        3. Temporal Gate (valid_to IS NULL)
        4. Decryption
        """
        if query_str is None:
            query_str = query

        from kryptodb.integrity.bootstrap_state import is_enabled as bootstrap_enabled
        bootstrap_mode = bootstrap_enabled()

        # 1 & 2: Intersect Permission and Activation Gates
        sk = self.session_keyring or keyring
        sk_keys = getattr(sk, "_keys", getattr(sk, "keys", {}))
        active_partitions = list(sk_keys.keys()) if isinstance(sk_keys, dict) else list(sk_keys)
        if not active_partitions and keyring._keys:
            active_partitions = list(keyring._keys.keys())

        if bootstrap_mode:
            effective_scopes = list(set(allowed_scopes))
        else:
            effective_scopes = list(set(allowed_scopes) & set(active_partitions))
        
        if not effective_scopes:
            return []
            
        # 3. Neo4j Query + Temporal Gate + Trust Ranking
        if subject_id:
            subject_search_indexes = [encryptor.compute_search_idx_session(scope, "subject", subject_id, self.session_keyring) for scope in effective_scopes]
            subject_search_indexes = [idx for idx in subject_search_indexes if idx is not None]
            legacy_blind_indexes = [encryptor.compute_blind_idx_session(scope, subject_id, self.session_keyring) for scope in effective_scopes]
            legacy_blind_indexes = [idx for idx in legacy_blind_indexes if idx is not None]
            
            if bootstrap_mode:
                legacy_blind_indexes.append(f"pending:{subject_id}")
                
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
                            
            if bootstrap_mode:
                # Add plaintext search against pending_envelope
                # We do a basic CONTAINS on the JSON string
                query = """
                MATCH (s)-[r:FACT]->(o)
                WHERE (
                    ANY(idx IN coalesce(r.search_indexes, []) WHERE idx IN $query_indexes)
                    OR (r.encryption_status = 'pending' AND r.pending_envelope CONTAINS $query_str)
                )
                  AND r.permission_scope IN $scopes
                  AND r.valid_to IS NULL AND r.revoked_at IS NULL
                RETURN s, r, o
                ORDER BY r.trust_weight DESC
                LIMIT 10
                """
                params = {"scopes": effective_scopes, "query_indexes": query_indexes, "query_str": query_str}
            else:
                if not query_indexes:
                    # Without encrypted search tokens there is no safe way
                    # to establish relevance. Returning arbitrary facts here
                    # would contaminate the model's context.
                    return []
                else:
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
            return []

        import uuid
        request_id = str(uuid.uuid4())
        
        try:
            if hasattr(neo4j_client, "execute_query_with_retry"):
                records, _, _ = neo4j_client.execute_query_with_retry(query, **params)
            else:
                records, _, _ = neo4j_client.driver.execute_query(query, **params)

            # An empty indexed result means that no graph fact matched the
            # query. Do not broaden this into an unrelated graph scan: those
            # facts would be presented to the model as out-of-context answers.
        except Exception:
            return []
            
        import asyncio
        from kryptodb.daemon.events import event_bus, GraphEvent
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
            
        from kryptodb.memory_api.session_search import session_index
        active_search_index = self.session_search_index or session_index
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
            is_unprotected = False
            
            if r_edge.get("encryption_status") == "pending" and r_edge.get("pending_envelope"):
                import json
                try:
                    knowledge = json.loads(r_edge["pending_envelope"])
                    is_unprotected = True
                except Exception:
                    pass
            elif r_edge.get("knowledge_ct") and r_edge.get("wrapped_dek"):
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
                
            # For pending records, fallback to the subject_id without "pending:" prefix if possible
            if not s_name and s_node.get("name_blind_idx", "").startswith("pending:"):
                s_name = s_node.get("name_blind_idx")[8:]
            if not o_name and o_node.get("name_blind_idx", "").startswith("pending:"):
                o_name = o_node.get("name_blind_idx")[8:]

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
            if is_unprotected:
                edge_dict["encryption_status"] = "unprotected"
                
            results.append(edge_dict)
            
            # Milestone 8.4 Phase A: Index into transient Bloom Filter for future substring search
            active_search_index.add_edge(edge_dict)

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
