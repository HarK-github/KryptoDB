"""Hybrid retrieval combining dense vector search and sparse BM25 fulltext with RRF."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from kryptodb.core.context import RequestContext
from kryptodb.core.embeddings.embedder import default_embedder
from kryptodb.core.ports import Embedder, MemoryStore, ScoredItem
from kryptodb.core.retrieval.query import parse_query

logger = logging.getLogger("kryptodb.core.retrieval.hybrid")


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[ScoredItem]],
    k: int = 60
) -> List[ScoredItem]:
    """Reciprocal Rank Fusion (RRF) across arbitrary ranked candidate streams.
    
    RRF(d) = sum( 1.0 / (k + rank_m(d)) ) for m in ranking streams.
    """
    scores: Dict[str, float] = {}
    items: Dict[str, ScoredItem] = {}

    for r_list in ranked_lists:
        for rank, item in enumerate(r_list):
            item_id = item.item_id
            rrf_contrib = 1.0 / (k + rank + 1)
            scores[item_id] = scores.get(item_id, 0.0) + rrf_contrib
            if item_id not in items:
                items[item_id] = item

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [
        ScoredItem(
            item_id=i,
            score=scores[i],
            item_type=items[i].item_type,
            text=items[i].text,
            metadata={**items[i].metadata, "rrf_score": scores[i]},
        )
        for i in sorted_ids
    ]


class HybridSearcher:
    """Hybrid search orchestrator executing vector, fulltext BM25, and exact matches."""

    def __init__(
        self,
        store: MemoryStore,
        embedder: Optional[Embedder] = None,
        rrf_k: int = 60
    ) -> None:
        self.store = store
        self.embedder = embedder or default_embedder
        self.rrf_k = rrf_k

    def search_candidates(
        self,
        query: str,
        context: RequestContext,
        top_k: int = 20
    ) -> List[ScoredItem]:
        """Orchestrates hybrid search over dense vector and sparse fulltext indexes."""
        parsed = parse_query(query)
        if not parsed.clean_text:
            return []

        # 1. Exact entity match lookup
        exact_items: List[ScoredItem] = []
        for candidate_name in [query.strip(), *parsed.quoted_entities, *parsed.tokens]:
            if not candidate_name:
                continue
            entity = self.store.find_entity(candidate_name, context)
            if entity:
                exact_items.append(
                    ScoredItem(
                        item_id=entity.id,
                        score=1.0,
                        item_type="entity",
                        text=entity.name,
                        metadata={"name": entity.name, "exact_match": True},
                    )
                )

        # 2. Vector search on facts
        query_vec = self.embedder.embed(query)
        vector_items = self.store.search_vector(query_vec, top_k=top_k, context=context)

        # 3. BM25 / Fulltext search on facts
        bm25_items = self.store.search_fulltext(parsed.clean_text, top_k=top_k, context=context)

        # 4. Fuse ranking streams using RRF
        ranking_streams = [r for r in [exact_items, vector_items, bm25_items] if r]
        if not ranking_streams:
            return []

        fused = reciprocal_rank_fusion(ranking_streams, k=self.rrf_k)
        return fused[:top_k]
