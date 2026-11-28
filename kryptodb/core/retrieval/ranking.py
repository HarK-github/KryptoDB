"""Three-stage ranking pipeline: Stage A pre-rank, Stage B cross-encoder, Stage C optional LLM."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from kryptodb.core.retrieval.provenance import ProvenanceStep, format_citation

logger = logging.getLogger("kryptodb.core.retrieval.ranking")


class AttributedMemoryResult(BaseModel):
    fact_id: str
    summary: str
    hops: int
    final_score: float
    relevance_score: float
    confidence: float
    recency_score: float
    citation_tag: str
    provenance_path: List[ProvenanceStep]


class PathReranker:
    """Three-stage ranking pipeline ensuring scale harmony and provenance attribution."""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        llm_rerank_fn: Optional[Callable[[str, List[str]], List[float]]] = None,
        cross_encoder: Optional[Any] = None,
    ):
        self.model_name = model_name
        self.llm_rerank_fn = llm_rerank_fn
        self._cross_encoder = cross_encoder
        self._use_fallback = True

        if self._cross_encoder is not None:
            self._use_fallback = False
        else:
            # No cross_encoder injected and none available — use lexical fallback.
            # In production, inject a CrossEncoderReranker from infrastructure/embeddings.
            self._use_fallback = True

    def compute_relevance_scores(self, query: str, candidate_texts: List[str]) -> List[float]:
        """Calculates semantic query-candidate relevance in [0, 1]."""
        if not candidate_texts:
            return []

        if not self._use_fallback and self._cross_encoder is not None:
            try:
                pairs = [[query, text] for text in candidate_texts]
                scores = self._cross_encoder.predict(pairs)
                # Sigmoid to normalize logits to [0, 1]
                return [1.0 / (1.0 + math.exp(-float(s))) for s in scores]
            except Exception as exc:
                logger.debug("CrossEncoder prediction error: %s. Using lexical fallback.", exc)

        # Lexical Jaccard / Overlap fallback
        q_tokens = set(re.findall(r"[a-zA-Z0-9]+", query.casefold()))
        scores = []
        for text in candidate_texts:
            t_tokens = set(re.findall(r"[a-zA-Z0-9]+", text.casefold()))
            if not t_tokens or not q_tokens:
                scores.append(0.1)
                continue
            direct_match = len(q_tokens & t_tokens)
            prefix_match = 0
            for q in q_tokens:
                if len(q) >= 4:
                    for t in t_tokens:
                        if len(t) >= 4 and (q.startswith(t) or t.startswith(q)) and q != t:
                            prefix_match += 0.5
                            break
            total_match = direct_match + prefix_match
            union_len = len(q_tokens | t_tokens)
            overlap = total_match / union_len if union_len > 0 else 0.0
            scores.append(min(1.0, max(0.05, overlap * 2.5)))
        return scores

    def compute_recency_score(self, created_at_val: Any, now: datetime, half_life_days: float = 30.0) -> float:
        """Exponential recency decay: exp(-ln(2) * age / half_life)."""
        if not created_at_val:
            return 0.8
        try:
            if isinstance(created_at_val, datetime):
                dt = created_at_val
            elif hasattr(created_at_val, "to_native"):
                dt = created_at_val.to_native()
            else:
                s = str(created_at_val).replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            return float(math.exp(-0.693147 * (age_days / half_life_days)))
        except Exception:
            return 0.8

    def rank_paths(
        self,
        query: str,
        paths: List[Dict[str, Any]],
        w_rel: float = 0.60,
        w_conf: float = 0.25,
        w_rec: float = 0.15,
        hop_penalty: float = 0.10,
        enable_llm_rerank: bool = False,
        top_k: int = 10,
        min_relevance_cutoff: float = 0.0,
        now: Optional[datetime] = None,
    ) -> List[AttributedMemoryResult]:
        """Executes Stage A pre-ranking, Stage B cross-encoder reranking, and composite scoring."""
        if not paths:
            return []

        dt_now = now or datetime.now(timezone.utc)

        # Stage A: DB Pre-rank (confidence * recency), keep top 30
        annotated_paths = []
        for p in paths:
            conf_val = p.get("confidence")
            mean_conf = float(conf_val if conf_val is not None else 1.0)
            rec_score = self.compute_recency_score(p.get("created_at"), dt_now)
            pre_score = mean_conf * rec_score
            annotated_paths.append({**p, "_pre_score": pre_score, "_rec_score": rec_score, "_mean_conf": mean_conf})

        annotated_paths.sort(key=lambda x: x["_pre_score"], reverse=True)
        stage_a_candidates = annotated_paths[:30]

        # Stage B: Cross-encoder rerank
        summaries = [p["summary"] for p in stage_a_candidates]
        rel_scores = self.compute_relevance_scores(query, summaries)

        # Stage C: Optional LLM reranking (if enabled and function provided)
        if enable_llm_rerank and self.llm_rerank_fn:
            try:
                llm_scores = self.llm_rerank_fn(query, summaries)
                # Blend 50/50 with cross-encoder
                rel_scores = [0.5 * cr + 0.5 * lr for cr, lr in zip(rel_scores, llm_scores)]
            except Exception as exc:
                logger.warning("Stage C LLM reranking failed: %s; keeping Stage B scores.", exc)

        attributed_results: List[AttributedMemoryResult] = []

        for p, s_rel in zip(stage_a_candidates, rel_scores):
            hops = int(p.get("hops", 1))
            mean_conf = p["_mean_conf"]
            s_rec = p["_rec_score"]

            # Composite final score with scale harmony (no double hop penalty)
            raw_score = (w_rel * s_rel) + (w_conf * mean_conf) + (w_rec * s_rec) - (hop_penalty * (hops - 1))
            final_score = min(1.0, max(0.0, float(raw_score)))

            path_edges = p.get("path_edges", [])
            steps: List[ProvenanceStep] = [
                ProvenanceStep(
                    hop=e.get("hop", 1),
                    source=e.get("source", ""),
                    predicate=e.get("predicate", ""),
                    target=e.get("target", ""),
                    edge_id=e.get("edge_id", ""),
                    description=e.get("description"),
                    scope=e.get("scope", "default"),
                    confidence=float(e.get("confidence", 1.0)),
                )
                for e in path_edges
            ]

            scopes = [e.scope for e in steps]
            edge_ids = [e.edge_id for e in steps]
            std_citation = format_citation(tenant_id=p.get("tenant_id", "default"), scopes=scopes, edge_ids=edge_ids)
            scope_label = ",".join(sorted(set(scopes))) if scopes else "default"
            edges_label = "->".join(edge_ids)
            citation = f"[Scope: {scope_label}, Edge: {edges_label}] {std_citation}"

            fact_id = edge_ids[0] if edge_ids else str(p.get("source_id", "fact-unknown"))

            attributed_results.append(
                AttributedMemoryResult(
                    fact_id=fact_id,
                    summary=p["summary"],
                    hops=hops,
                    final_score=round(final_score, 4),
                    relevance_score=round(s_rel, 4),
                    confidence=round(mean_conf, 4),
                    recency_score=round(s_rec, 4),
                    citation_tag=citation,
                    provenance_path=steps,
                )
            )

        attributed_results.sort(key=lambda x: x.final_score, reverse=True)
        if min_relevance_cutoff > 0.0:
            attributed_results = [r for r in attributed_results if r.relevance_score >= min_relevance_cutoff]
        return attributed_results[:top_k]


path_reranker = PathReranker()
