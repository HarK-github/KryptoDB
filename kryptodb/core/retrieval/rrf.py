"""Reciprocal Rank Fusion (RRF) for combining vector and lexical rankings."""

from __future__ import annotations

from typing import Any, List, Sequence

from kryptodb.core.ports import ScoredItem
from kryptodb.core.retrieval.hybrid import reciprocal_rank_fusion


def _to_scored_item(item: Any) -> ScoredItem:
    if isinstance(item, ScoredItem):
        return item
    if isinstance(item, dict):
        item_id = str(item.get("id") or item.get("name") or item.get("item_id"))
        return ScoredItem(
            item_id=item_id,
            score=float(item.get("score", 0.0)),
            item_type=str(item.get("item_type", "fact")),
            text=str(item.get("text") or item.get("description") or ""),
            metadata=dict(item.get("metadata", {})),
        )
    return ScoredItem(item_id=str(item), score=0.0, item_type="fact", text=str(item))


def rrf_fuse(
    rank_lists: Sequence[Sequence[Any]],
    k: int = 60,
    top_k: int = 10,
) -> List[ScoredItem]:
    """Combines multiple ranked lists into a fused ranking using Reciprocal Rank Fusion."""
    normalized = [[_to_scored_item(x) for x in r_list] for r_list in rank_lists]
    return reciprocal_rank_fusion(normalized, k=k)[:top_k]
