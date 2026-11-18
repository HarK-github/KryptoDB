import math
from typing import List, Set, Any

def compute_recall_at_k(retrieved: List[str], ground_truth: Set[str], k: int) -> float:
    """Computes Recall@K: fraction of relevant items retrieved in top-k."""
    if not ground_truth:
        return 1.0 if not retrieved else 0.0
    top_k = set(retrieved[:k])
    hits = len(top_k & ground_truth)
    return hits / len(ground_truth)

def compute_precision_at_k(retrieved: List[str], ground_truth: Set[str], k: int) -> float:
    """Computes Precision@K: fraction of top-k retrieved items that are relevant."""
    if k <= 0 or not retrieved:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in ground_truth)
    return hits / len(top_k)

def compute_ndcg_at_k(retrieved: List[str], ground_truth: Set[str], k: int) -> float:
    """Computes Normalized Discounted Cumulative Gain (nDCG@K)."""
    if not ground_truth:
        return 1.0 if not retrieved else 0.0
        
    top_k = retrieved[:k]
    dcg = 0.0
    for idx, item in enumerate(top_k):
        rel = 1.0 if item in ground_truth else 0.0
        if rel > 0:
            dcg += rel / math.log2(idx + 2) # rank is idx + 1, log2(rank + 1) -> log2(idx + 2)
            
    # Ideal DCG: all ground truth items appear at the very top
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(ground_truth))))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg

def compute_mrr(retrieved: List[str], ground_truth: Set[str]) -> float:
    """Computes Mean Reciprocal Rank (MRR): 1 / rank of first relevant item."""
    if not ground_truth:
        return 1.0 if not retrieved else 0.0
    for idx, item in enumerate(retrieved):
        if item in ground_truth:
            return 1.0 / (idx + 1)
    return 0.0

