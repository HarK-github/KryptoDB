"""Reciprocal Rank Fusion (RRF) for combining vector, lexical, and entity rankings."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Union

from kryptodb.core.ports import ScoredItem


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


def reciprocal_rank_fusion(
    *ranked_lists: Any,
    k: int = 60,
) -> List[Any]:
    """Reciprocal Rank Fusion (RRF) across arbitrary ranked candidate streams.

    Supports both:
      - positional streams: reciprocal_rank_fusion(stream1, stream2, k=60)
      - iterable of streams: reciprocal_rank_fusion([stream1, stream2], k=60)
    Works transparently with sequences of ScoredItem or sequences of dicts.
    """
    streams: List[Sequence[Any]] = []
    if len(ranked_lists) == 1 and isinstance(ranked_lists[0], (list, tuple)):
        first = ranked_lists[0]
        if first and isinstance(first[0], (list, tuple)):
            streams = list(first)
        else:
            streams = [first]
    else:
        streams = list(ranked_lists)

    if not streams:
        return []

    # Detect whether inputs are dicts or ScoredItems
    first_item = None
    for st in streams:
        if st:
            first_item = st[0]
            break

    is_dict = isinstance(first_item, dict)

    scores: Dict[str, float] = {}
    items: Dict[str, Any] = {}

    for stream_idx, r_list in enumerate(streams):
        for rank, item in enumerate(r_list):
            if is_dict:
                item_id = str(item.get("id") or item.get("name") or item.get("item_id"))
                scores[item_id] = scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))
                if item_id not in items:
                    items[item_id] = dict(item)
                if stream_idx == 0:
                    items[item_id]["vector_rank"] = rank + 1
                elif stream_idx == 1:
                    items[item_id]["bm25_rank"] = rank + 1
            else:
                item_id = item.item_id if isinstance(item, ScoredItem) else str(item)
                scores[item_id] = scores.get(item_id, 0.0) + (1.0 / (k + rank + 1))
                if item_id not in items:
                    items[item_id] = item

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    if is_dict:
        return [{**items[i], "rrf_score": scores[i]} for i in sorted_ids]
    else:
        return [
            ScoredItem(
                item_id=i,
                score=scores[i],
                item_type=items[i].item_type if isinstance(items[i], ScoredItem) else "fact",
                text=items[i].text if isinstance(items[i], ScoredItem) else str(items[i]),
                metadata={
                    **(items[i].metadata if isinstance(items[i], ScoredItem) else {}),
                    "rrf_score": scores[i],
                },
            )
            for i in sorted_ids
        ]


def rrf_fuse(
    rank_lists: Sequence[Sequence[Any]],
    k: int = 60,
    top_k: int = 10,
) -> List[ScoredItem]:
    """Combines multiple ranked lists into a fused ranking using Reciprocal Rank Fusion."""
    normalized = [[_to_scored_item(x) for x in r_list] for r_list in rank_lists]
    return reciprocal_rank_fusion(normalized, k=k)[:top_k]
