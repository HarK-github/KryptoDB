from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from kryptodb.infrastructure.embeddings import embedding_service
from kryptodb.storage.neo4j_client import neo4j_client

logger = logging.getLogger("kryptodb.hybrid_search")

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "to", "in", "on", "at",
    "by", "for", "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "from", "up", "down", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "what", "which", "who", "whom", "this",
    "that", "these", "those", "am", "been", "being", "have", "has", "had", "having",
    "do", "does", "did", "doing", "tell", "me"
}

def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    k: int = 60
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion (RRF) algorithm to fuse rank lists from
    dense vector search and sparse BM25 fulltext search.
    RRF_score(d) = sum( 1.0 / (k + rank(d)) )
    """
    scores: Dict[str, float] = {}
    items: Dict[str, Dict[str, Any]] = {}

    for rank, item in enumerate(vector_results):
        item_id = str(item.get("id") or item.get("name"))
        scores[item_id] = scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))
        items[item_id] = {**item, "vector_rank": rank + 1}

    for rank, item in enumerate(bm25_results):
        item_id = str(item.get("id") or item.get("name"))
        scores[item_id] = scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))
        if item_id in items:
            items[item_id]["bm25_rank"] = rank + 1
        else:
            items[item_id] = {**item, "bm25_rank": rank + 1}

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [{**items[i], "rrf_score": scores[i]} for i in sorted_ids]

class HybridSearchEngine:
    def __init__(self, client: Any = None):
        self.client = client or neo4j_client

    def find_seed_entities(
        self,
        query: str,
        tenant_id: str = "default",
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Executes parallel vector and fulltext BM25 search on Neo4j Entity nodes
        and fuses results with RRF.
        """
        if not query or not query.strip():
            return []

        query_vec = embedding_service.embed_query(query)
        vector_results: List[Dict[str, Any]] = []
        bm25_results: List[Dict[str, Any]] = []

        # 1. Vector Search on Entity nodes
        vector_query = """
        CALL db.index.vector.queryNodes('entity_embedding_idx', $top_k, $query_vec)
        YIELD node, score
        WHERE node.tenant_id = $tenant_id
        RETURN node.id AS id, node.name AS name, score AS vector_score
        """
        try:
            records, _, _ = self.client.execute_query_with_retry(
                vector_query,
                top_k=top_k * 2,
                query_vec=query_vec,
                tenant_id=tenant_id
            )
            for r in records:
                vector_results.append({
                    "id": r["id"],
                    "name": r["name"],
                    "score": float(r["vector_score"])
                })
        except Exception as exc:
            # Fallback to alternate index name if entity_embedding_idx not found
            try:
                alt_query = """
                CALL db.index.vector.queryNodes('entity_vector_index', $top_k, $query_vec)
                YIELD node, score
                WHERE node.tenant_id = $tenant_id
                RETURN node.id AS id, node.name AS name, score AS vector_score
                """
                records, _, _ = self.client.execute_query_with_retry(
                    alt_query,
                    top_k=top_k * 2,
                    query_vec=query_vec,
                    tenant_id=tenant_id
                )
                for r in records:
                    vector_results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "score": float(r["vector_score"])
                    })
            except Exception:
                logger.debug("Vector search fallback (index may not exist): %s", exc)
                # Fallback to direct name match or cosine search in-memory
                fallback_query = """
                MATCH (n:Entity)
                WHERE n.tenant_id = $tenant_id AND toLower(n.name) CONTAINS toLower($term)
                RETURN n.id AS id, n.name AS name
                LIMIT $top_k
                """
                try:
                    records, _, _ = self.client.execute_query_with_retry(
                        fallback_query,
                        tenant_id=tenant_id,
                        term=query.strip().split()[0] if query.strip() else "",
                        top_k=top_k
                    )
                    for r in records:
                        vector_results.append({"id": r["id"], "name": r["name"], "score": 0.5})
                except Exception:
                    pass

        # 2. Fulltext BM25 Search on Entity nodes
        # Extract meaningful terms excluding stop words
        safe_lucene = "".join([c if c.isalnum() or c in " _-" else " " for c in query]).strip()
        clean_terms = [t.strip("-") for t in safe_lucene.split() if t.strip("-").lower() not in STOP_WORDS and len(t.strip("-")) > 1]
        if not clean_terms:
            clean_terms = [t.strip("-") for t in safe_lucene.split() if len(t.strip("-")) > 1]

        if clean_terms:
            lucene_query = " OR ".join([f"{term}*" for term in clean_terms])
            fulltext_query = """
            CALL db.index.fulltext.queryNodes('entity_name_ft', $lucene_query)
            YIELD node, score
            WHERE node.tenant_id = $tenant_id
            RETURN node.id AS id, node.name AS name, score AS bm25_score
            LIMIT $top_k
            """
            try:
                records, _, _ = self.client.execute_query_with_retry(
                    fulltext_query,
                    lucene_query=lucene_query,
                    tenant_id=tenant_id,
                    top_k=top_k * 2
                )
                for r in records:
                    bm25_results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "score": float(r["bm25_score"])
                    })
            except Exception as exc:
                logger.debug("Fulltext search fallback: %s", exc)

        # 3. Fuse with RRF
        fused = reciprocal_rank_fusion(vector_results, bm25_results, k=60)
        return fused[:top_k]

hybrid_search_engine = HybridSearchEngine()
