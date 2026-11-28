"""Plaintext GraphRAG retriever implementing core.ports.Retriever.

Encapsulates the full Hybrid RRF + 2-hop traversal + retrospective edges + cross-encoder
rerank pipeline for the plaintext (non-encrypted) storage mode.
Injected into RetrievalService by wire_runtime_services() in kryptodb/daemon/app.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from kryptodb.core.context import RequestContext
from kryptodb.core.retrieval.hybrid_search import HybridSearchEngine
from kryptodb.core.retrieval.ranking import AttributedMemoryResult, PathReranker
from kryptodb.core.retrieval.traversal import MultiHopTraverser

logger = logging.getLogger("kryptodb.infrastructure.neo4j.plaintext_retriever")


class PlaintextNeo4jRetriever:
    """Plaintext GraphRAG pipeline implementing the core.ports.Retriever protocol.

    Pipeline:
      1. Hybrid RRF seed discovery (vector + BM25)
      2. 2-hop Cypher traversal with cycle/hub suppression
      3. Retrospective edge fetch
      4. Cross-encoder reranking
    """

    def __init__(
        self,
        hybrid_engine: HybridSearchEngine,
        traverser: MultiHopTraverser,
        reranker: PathReranker,
        client: Any,
        retrospective_query: str = "",
    ) -> None:
        self.hybrid_engine = hybrid_engine
        self.traverser = traverser
        self.reranker = reranker
        self.client = client
        self._retrospective_query = retrospective_query

    def search(
        self,
        query: str,
        context: RequestContext,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
    ) -> List[AttributedMemoryResult]:
        """Execute the full GraphRAG pipeline and return attributed, reranked results."""
        if not query or not query.strip():
            return []

        from kryptodb.core.memory.entity_resolution import normalize_entity_name
        from kryptodb.core.settings import settings

        dt_now = context.now or datetime.now(timezone.utc)
        tenant_id = context.tenant_id
        active_scopes = list(context.active_scopes) if context.active_scopes else None
        effective_cutoff = relevance_cutoff if relevance_cutoff is not None else settings.recall_relevance_cutoff
        effective_cap = top_k if top_k is not None else settings.recall_max_facts

        # 1. Seed discovery via Hybrid RRF
        seeds = self.hybrid_engine.find_seed_entities(
            query=query,
            tenant_id=tenant_id,
            active_scopes=active_scopes,
            top_k=5,
        )
        if not seeds:
            seeds = self.hybrid_engine.find_semantic_fallback_seeds(
                query=query,
                tenant_id=tenant_id,
                active_scopes=active_scopes,
                top_k=5,
            )

        if not seeds:
            return []

        seed_ids = [s["id"] for s in seeds if "id" in s]
        seed_names = [s["name"] for s in seeds if "name" in s]

        # 2. Multi-hop traversal
        max_1hop = 10 if max_hops >= 1 else 0
        max_2hop = 15 if max_hops >= 2 else 0
        paths = self.traverser.traverse_from_seeds(
            seed_entity_ids=seed_ids,
            context=context,
            tenant_id=tenant_id,
            active_scopes=active_scopes,
            max_1hop=max_1hop,
            max_2hop=max_2hop,
            now=dt_now,
        )

        # 3. Retrospective edges
        retro_paths = self._fetch_retrospective(seed_ids, seed_names, tenant_id, active_scopes)
        all_paths = paths + retro_paths

        if not all_paths:
            return []

        # 4. Rerank
        ranked = self.reranker.rank_paths(
            query=query,
            paths=all_paths,
            top_k=effective_cap * 2,
            min_relevance_cutoff=effective_cutoff if effective_cutoff else 0.0,
            now=dt_now,
        )

        # Deduplicate by summary
        seen_summaries: set = set()
        deduped: List[AttributedMemoryResult] = []
        for r in ranked:
            norm = " ".join(r.summary.strip().casefold().split())
            if norm not in seen_summaries:
                seen_summaries.add(norm)
                deduped.append(r)

        return deduped[:effective_cap]

    def _fetch_retrospective(
        self,
        seed_ids: List[str],
        seed_names: List[str],
        tenant_id: str,
        active_scopes: Optional[List[str]],
        limit: int = 15,
    ) -> list:
        if not self._retrospective_query:
            return []
        if not self.client or (not getattr(self.client, "driver", None) and not hasattr(self.client, "execute_query_with_retry")):
            return []
        try:
            params = {
                "seed_ids": seed_ids,
                "seed_names": seed_names,
                "tenant_id": tenant_id,
                "active_scopes": active_scopes,
                "limit": limit,
            }
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(self._retrospective_query, **params)
            else:
                records, _, _ = self.client.driver.execute_query(self._retrospective_query, **params)

            paths = []
            for r in records:
                s_name = r.get("s_name") or ""
                t_name = r.get("t_name") or ""
                pred = r.get("predicate") or ""
                status = r.get("status") or "active"
                paths.append({
                    "hops": 1,
                    "source_id": r.get("s_id"),
                    "source_name": s_name,
                    "target_id": r.get("t_id"),
                    "target_name": t_name,
                    "summary": f"[status: {status}] {s_name} {pred} {t_name}",
                    "confidence": float(r.get("confidence") or 1.0),
                    "trust_weight": float(r.get("trust_weight") or 1.0),
                    "created_at": r.get("created_at"),
                    "path_edges": [{
                        "hop": 1,
                        "source": s_name,
                        "predicate": pred,
                        "target": t_name,
                        "edge_id": r.get("edge_id"),
                        "description": r.get("description"),
                        "scope": r.get("scope", "default"),
                        "confidence": float(r.get("confidence") or 1.0),
                        "status": status,
                    }],
                })
            return paths
        except Exception as exc:
            logger.warning("Retrospective edge fetch failed: %s", exc)
            return []
