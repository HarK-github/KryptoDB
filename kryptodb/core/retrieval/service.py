"""Unified retrieval service: hybrid search, cycle-safe multi-hop traversal, and 3-stage ranking."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kryptodb.core.observability import tracer
from kryptodb.core.retrieval.hybrid_search import HybridSearchEngine, hybrid_search_engine
from kryptodb.core.retrieval.traversal import MultiHopTraverser, multihop_traverser
from kryptodb.core.retrieval.ranking import AttributedMemoryResult, PathReranker, path_reranker
from kryptodb.storage.neo4j_client import neo4j_client

logger = logging.getLogger("kryptodb.core.retrieval.service")


class RetrievalService:
    """Unified retrieval service providing hybrid vector + BM25 seed discovery,

    2-hop Cypher multi-hop traversal with cycle control, and composite cross-encoder reranking.
    """

    def __init__(
        self,
        hybrid_engine: Optional[HybridSearchEngine] = None,
        traverser: Optional[MultiHopTraverser] = None,
        reranker: Optional[PathReranker] = None,
        client: Any = None,
    ):
        self.client = client or neo4j_client
        self.hybrid_engine = hybrid_engine or hybrid_search_engine
        self.traverser = traverser or multihop_traverser
        self.reranker = reranker or path_reranker

    def search(
        self,
        query: str,
        tenant_id: str = "default",
        active_scopes: Optional[List[str]] = None,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> List[AttributedMemoryResult]:
        with tracer.start_as_current_span("retrieval.search") as span:
            span.set_attribute("query", query)
            span.set_attribute("tenant_id", tenant_id)
            return self._search_graphrag(
                query, tenant_id, active_scopes, top_k, max_hops, relevance_cutoff, now
            )

    def _search_graphrag(
        self,
        query: str,
        tenant_id: str = "default",
        active_scopes: Optional[List[str]] = None,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> List[AttributedMemoryResult]:
        if not query or not query.strip():
            return []

        import re
        from kryptodb.core.memory.entity_resolution import normalize_entity_name
        from kryptodb.core.settings import settings

        dt_now = now or datetime.now(timezone.utc)
        effective_cutoff = relevance_cutoff if relevance_cutoff is not None else settings.recall_relevance_cutoff
        effective_cap = top_k if top_k is not None else settings.recall_max_facts

        # 1. Discover top seed entities via Hybrid RRF
        with tracer.start_as_current_span("retrieval.seed_discovery"):
            seeds = self.hybrid_engine.find_seed_entities(
                query=query,
                tenant_id=tenant_id,
                top_k=5,
            )
        if not seeds:
            logger.debug("No seed entities found for query: %s", query)
            return []

        seed_ids = [s["id"] for s in seeds]

        # 2. Stratified 1-hop & 2-hop traversal with cycle control
        with tracer.start_as_current_span("retrieval.multihop_traversal"):
            max_1hop = max(effective_cap * 2, 10)
            max_2hop = max(effective_cap * 2, 15) if max_hops >= 2 else 0
            candidate_paths = self.traverser.traverse_from_seeds(
                seed_entity_ids=seed_ids,
                tenant_id=tenant_id,
                active_scopes=active_scopes,
                max_1hop=max_1hop,
                max_2hop=max_2hop,
                now=dt_now,
            )

        if not candidate_paths:
            return []

        # Filter out synthetic query logging nodes and placeholder objects
        clean_paths = []
        for p in candidate_paths:
            edges = p.get("path_edges", [])
            has_synthetic = any(
                e.get("predicate") in ("QUERY", "PROCESSED_QUERY", "PROCESSED_MESSAGE")
                or str(e.get("target")).startswith("scope:")
                or str(e.get("target")).lower() in ("value", "object", "none", "null", "undefined")
                for e in edges
            )
            if not has_synthetic:
                clean_paths.append(p)

        if not clean_paths:
            return []

        # 3. Composite 3-Stage Reranking with relevance cutoff
        with tracer.start_as_current_span("retrieval.reranking"):
            ranked_results = self.reranker.rank_paths(
                query=query,
                paths=clean_paths,
                top_k=effective_cap * 2,
                min_relevance_cutoff=effective_cutoff,
                now=dt_now,
            )

        # Deduplicate near-identical summaries
        seen_summaries = set()
        deduped = []
        for r in ranked_results:
            norm = " ".join(r.summary.strip().casefold().split())
            if norm not in seen_summaries:
                seen_summaries.add(norm)
                deduped.append(r)

        return deduped[:effective_cap]


retrieval_service = RetrievalService()
