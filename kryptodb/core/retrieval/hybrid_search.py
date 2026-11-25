from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Optional

from kryptodb.core.embeddings.embedder import default_embedder
from kryptodb.core.ports import Embedder
from kryptodb.core.retrieval.rrf import reciprocal_rank_fusion

logger = logging.getLogger("kryptodb.hybrid_search")


def _get_neo4j_client() -> Any:
    try:
        mod = importlib.import_module("kryptodb.infrastructure.neo4j.client")
        return getattr(mod, "neo4j_client", None)
    except Exception:
        return None


def _get_query(query_name: str) -> str:
    try:
        mod = importlib.import_module("kryptodb.infrastructure.neo4j.queries")
        return getattr(mod, query_name, "")
    except Exception:
        return ""


STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "to", "in", "on", "at",
    "by", "for", "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "from", "up", "down", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "what", "which", "who", "whom", "this",
    "that", "these", "those", "am", "been", "being", "have", "has", "had", "having",
    "do", "does", "did", "doing", "tell", "me",
    "next", "last", "previous", "prior", "week", "month", "year", "day", "time"
}

class HybridSearchEngine:
    def __init__(self, client: Any = None, embedder: Optional[Embedder] = None):
        self._client = client
        self.embedder = embedder or default_embedder

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return _get_neo4j_client()

    def _embed_query(self, query: str) -> List[float]:
        if self.embedder is not None:
            if hasattr(self.embedder, "embed_query"):
                return self.embedder.embed_query(query)
            if hasattr(self.embedder, "embed"):
                return self.embedder.embed(query)
        return default_embedder.embed(query)

    def find_seed_entities(
        self,
        query: str,
        tenant_id: str = "default",
        top_k: int = 10,
        active_scopes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes parallel vector and fulltext BM25 search on Neo4j Entity nodes
        and fuses results with RRF. If 0 seeds found, falls back to semantic vector search.
        """
        if not query or not query.strip():
            return []

        query_vec = self._embed_query(query)
        vector_results: List[Dict[str, Any]] = []
        bm25_results: List[Dict[str, Any]] = []

        # 1. Vector Search on Entity nodes
        vector_query = _get_query("HYBRID_VECTOR_ENTITY_QUERY")
        try:
            records, _, _ = self.client.execute_query_with_retry(
                vector_query,
                top_k=top_k * 2,
                query_vec=query_vec,
                tenant_id=tenant_id,
                active_scopes=active_scopes,
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
                alt_query = _get_query("HYBRID_VECTOR_ENTITY_ALT_QUERY")
                records, _, _ = self.client.execute_query_with_retry(
                    alt_query,
                    top_k=top_k * 2,
                    query_vec=query_vec,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                )
                for r in records:
                    vector_results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "score": float(r["vector_score"])
                    })
            except Exception:
                logger.debug("Vector search index not found: %s", exc)

        # 2. Fulltext BM25 Search on Entity nodes
        # Extract meaningful terms excluding stop words
        safe_lucene = "".join([c if c.isalnum() or c in " _-" else " " for c in query]).strip()
        clean_terms = [t.strip("-") for t in safe_lucene.split() if t.strip("-").lower() not in STOP_WORDS and len(t.strip("-")) > 1]
        if not clean_terms:
            clean_terms = [t.strip("-") for t in safe_lucene.split() if len(t.strip("-")) > 1]

        if clean_terms:
            lucene_query = " OR ".join([f"{term}*" for term in clean_terms])
            fulltext_query = _get_query("HYBRID_FULLTEXT_ENTITY_QUERY")
            try:
                records, _, _ = self.client.execute_query_with_retry(
                    fulltext_query,
                    lucene_query=lucene_query,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
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
        if not fused:
            return self.find_semantic_fallback_seeds(
                query=query,
                tenant_id=tenant_id,
                active_scopes=active_scopes,
                top_k=top_k,
            )
        return fused[:top_k]

    def find_semantic_fallback_seeds(
        self,
        query: str,
        tenant_id: str = "default",
        active_scopes: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Semantic Seed Fallback: When find_seed_entities() returns 0 seeds
        (because query has zero lexical overlap with entity names), falls back
        to vector similarity search over stored fact/entity embeddings in the
        active scope to select top-k seed entities.
        """
        if not query or not query.strip():
            return []

        query_vec = self._embed_query(query)
        seeds_by_id: Dict[str, Dict[str, Any]] = {}

        # 1. Cosine similarity over Entity embeddings in active scope
        entity_cypher = _get_query("SEMANTIC_SEED_FALLBACK_ENTITY_QUERY")
        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(
                    entity_cypher,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    query_vec=query_vec,
                    top_k=top_k * 2,
                )
            elif getattr(self.client, "driver", None):
                records, _, _ = self.client.driver.execute_query(
                    entity_cypher,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    query_vec=query_vec,
                    top_k=top_k * 2,
                )
            else:
                records = []

            for r in records:
                e_id = r["id"]
                score = float(r["score"])
                seeds_by_id[e_id] = {
                    "id": e_id,
                    "name": r["name"],
                    "score": score,
                    "fallback": True,
                }
        except Exception as exc:
            logger.debug("Entity semantic seed fallback failed: %s", exc)

        # 2. Cosine similarity over Fact embeddings in active scope
        fact_cypher = _get_query("SEMANTIC_SEED_FALLBACK_FACT_QUERY")
        try:
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(
                    fact_cypher,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    query_vec=query_vec,
                    top_k=top_k * 2,
                )
            elif getattr(self.client, "driver", None):
                records, _, _ = self.client.driver.execute_query(
                    fact_cypher,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    query_vec=query_vec,
                    top_k=top_k * 2,
                )
            else:
                records = []

            for r in records:
                e_id = r["id"]
                score = float(r["score"])
                if e_id in seeds_by_id:
                    seeds_by_id[e_id]["score"] = max(seeds_by_id[e_id]["score"], score)
                else:
                    seeds_by_id[e_id] = {
                        "id": e_id,
                        "name": r["name"],
                        "score": score,
                        "fallback": True,
                    }
        except Exception as exc:
            logger.debug("Fact semantic seed fallback failed: %s", exc)

        sorted_seeds = sorted(seeds_by_id.values(), key=lambda x: x["score"], reverse=True)
        return sorted_seeds[:top_k]

hybrid_search_engine = HybridSearchEngine()

