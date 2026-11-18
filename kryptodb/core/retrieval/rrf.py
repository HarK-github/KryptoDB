"""Reciprocal Rank Fusion (RRF) for combining vector and lexical rankings."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence
from kryptodb.core.ports import ScoredItem


def rrf_fuse(
    rank_lists: Sequence[Sequence[Any]],
    k: int = 60,
    top_k: int = 10,
) -> List[ScoredItem]:
    """Combines multiple ranked lists into a fused ranking using Reciprocal Rank Fusion.

    RRF_score(d) = sum( 1.0 / (k + rank(d)) )
    """
    scores: Dict[str, float] = {}
    item_map: Dict[str, ScoredItem] = {}

    for rank_list in rank_lists:
        for rank, item in enumerate(rank_list):
            if isinstance(item, ScoredItem):
                item_id = item.item_id
                obj = item
            elif isinstance(item, dict):
                item_id = str(item.get("id") or item.get("name") or item.get("item_id"))
                obj = ScoredItem(
                    item_id=item_id,
                    score=float(item.get("score", 0.0)),
                    item_type=str(item.get("item_type", "fact")),
                    text=str(item.get("text") or item.get("description") or ""),
                    metadata=dict(item.get("metadata", {})),
                )
            else:
                item_id = str(item)
                obj = ScoredItem(item_id=item_id, score=0.0, item_type="fact", text=str(item))

            scores[item_id] = scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))
            if item_id not in item_map:
                item_map[item_id] = obj

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    fused_results = []
    for item_id in sorted_ids[:top_k]:
        base_item = item_map[item_id]
        fused_results.append(
            ScoredItem(
                item_id=base_item.item_id,
                score=scores[item_id],
                item_type=base_item.item_type,
                text=base_item.text,
                metadata=base_item.metadata,
            )
        )
    return fused_results
