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

logger = logging.getLogger("kryptodb.core.retrieval.service")

# Injected from infrastructure at startup by wire_runtime_services().
_RETROSPECTIVE_FACTS_QUERY: str = ""


def configure_retrieval_queries(retrospective_query: str) -> None:
    """Called once from kryptodb/daemon/app.py to inject Cypher query strings."""
    global _RETROSPECTIVE_FACTS_QUERY
    _RETROSPECTIVE_FACTS_QUERY = retrospective_query


class RetrievalService:
    """Unified retrieval service providing hybrid vector + BM25 seed discovery,

    2-hop Cypher multi-hop traversal with cycle control, and composite cross-encoder reranking.
    Delegates to an injected ``Retriever`` port when set; falls back to the built-in GraphRAG
    pipeline otherwise.
    """

    def __init__(
        self,
        hybrid_engine: Optional[HybridSearchEngine] = None,
        traverser: Optional[MultiHopTraverser] = None,
        reranker: Optional[PathReranker] = None,
        client: Any = None,
        retriever: Any = None,
    ):
        self.client = client  # injected; None → retrospective-edge queries silently skipped
        self.hybrid_engine = hybrid_engine or hybrid_search_engine
        self.traverser = traverser or multihop_traverser
        self.reranker = reranker or path_reranker
        self.retriever = retriever  # optional Retriever port (plaintext or encrypted)

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
            if self.retriever is not None:
                from kryptodb.core.context import RequestContext
                scopes_to_query = [s for s in (active_scopes or ["default"]) if s and s != "*"] or ["default"]
                ctx = RequestContext.create(
                    tenant_id=tenant_id,
                    agent_id="retrieval-service",
                    active_scopes=scopes_to_query,
                    now=now,
                )
                return self.retriever.search(
                    query=query,
                    context=ctx,
                    top_k=top_k,
                    max_hops=max_hops,
                    relevance_cutoff=relevance_cutoff,
                )
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
                active_scopes=active_scopes,
                top_k=5,
            )
            is_semantic_fallback = False
            if not seeds:
                logger.info("Zero seeds found from lexical/entity search; falling back to semantic vector similarity search over scope %s", active_scopes)
                seeds = self.hybrid_engine.find_semantic_fallback_seeds(
                    query=query,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    top_k=5,
                )
                is_semantic_fallback = bool(seeds)
            elif any(s.get("fallback") for s in seeds):
                is_semantic_fallback = True

        if not seeds:
            logger.debug("No seed entities found for query: %s", query)
            return []

        seed_ids = [s["id"] for s in seeds]
        seed_names = [s.get("name") for s in seeds if s.get("name")]

        # Lightweight temporal intent check for retrospective queries
        retrospective_patterns = [
            r"\bbefore\b",
            r"\bpreviously\b",
            r"\bused to\b",
            r"\bprior\b",
            r"\bhistory\b",
            r"\bhistorical\b",
            r"\bearlier\b",
            r"\bpast\b",
            r"\bformerly\b",
        ]
        is_retrospective = any(re.search(pat, query, re.IGNORECASE) for pat in retrospective_patterns)

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

            # Temporal Routing: When query is retrospective, include superseded edges with temporal annotations
            if is_retrospective:
                retrospective_paths = self._fetch_retrospective_edges(
                    seed_ids=seed_ids,
                    seed_names=seed_names,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    limit=max_1hop,
                )
                if retrospective_paths:
                    for p in candidate_paths:
                        if not p["summary"].startswith("[status:"):
                            p["summary"] = f"[status: active] {p['summary']}"
                    retro_edge_ids = {
                        rp["path_edges"][0]["edge_id"] for rp in retrospective_paths if rp.get("path_edges")
                    }
                    candidate_paths = retrospective_paths + [
                        p for p in candidate_paths
                        if not any(e.get("edge_id") in retro_edge_ids for e in p.get("path_edges", []))
                    ]

        if not candidate_paths and not is_semantic_fallback:
            fallback_seeds = self.hybrid_engine.find_semantic_fallback_seeds(
                query=query,
                tenant_id=tenant_id,
                active_scopes=active_scopes,
                top_k=5,
            )
            if fallback_seeds:
                is_semantic_fallback = True
                fb_seed_ids = [s["id"] for s in fallback_seeds]
                candidate_paths = self.traverser.traverse_from_seeds(
                    seed_entity_ids=fb_seed_ids,
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
                min_relevance_cutoff=0.0 if (is_retrospective or is_semantic_fallback) else effective_cutoff,
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

        if not deduped and not is_semantic_fallback:
            fallback_seeds = self.hybrid_engine.find_semantic_fallback_seeds(
                query=query,
                tenant_id=tenant_id,
                active_scopes=active_scopes,
                top_k=5,
            )
            if fallback_seeds:
                fb_seed_ids = [s["id"] for s in fallback_seeds]
                fb_paths = self.traverser.traverse_from_seeds(
                    seed_entity_ids=fb_seed_ids,
                    tenant_id=tenant_id,
                    active_scopes=active_scopes,
                    max_1hop=max_1hop,
                    max_2hop=max_2hop,
                    now=dt_now,
                )
                if fb_paths:
                    fb_clean = [
                        p for p in fb_paths
                        if not any(
                            e.get("predicate") in ("QUERY", "PROCESSED_QUERY", "PROCESSED_MESSAGE")
                            or str(e.get("target")).startswith("scope:")
                            or str(e.get("target")).lower() in ("value", "object", "none", "null", "undefined")
                            for e in p.get("path_edges", [])
                        )
                    ]
                    if fb_clean:
                        fb_ranked = self.reranker.rank_paths(
                            query=query,
                            paths=fb_clean,
                            top_k=effective_cap * 2,
                            min_relevance_cutoff=0.0,
                            now=dt_now,
                        )
                        for r in fb_ranked:
                            norm = " ".join(r.summary.strip().casefold().split())
                            if norm not in seen_summaries:
                                seen_summaries.add(norm)
                                deduped.append(r)

        return deduped[:effective_cap]

    def _fetch_retrospective_edges(
        self,
        seed_ids: List[str],
        seed_names: List[str],
        tenant_id: str,
        active_scopes: Optional[List[str]],
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        """Queries for superseded and active facts with temporal validity bounds for retrospective queries."""
        if not getattr(self.client, "driver", None) and not hasattr(self.client, "execute_query_with_retry"):
            return []

        retro_query = _RETROSPECTIVE_FACTS_QUERY
        try:
            params = {
                "seed_ids": seed_ids,
                "seed_names": seed_names,
                "tenant_id": tenant_id,
                "active_scopes": active_scopes,
                "limit": limit,
            }
            if hasattr(self.client, "execute_query_with_retry"):
                records, _, _ = self.client.execute_query_with_retry(retro_query, **params)
            else:
                records, _, _ = self.client.driver.execute_query(retro_query, **params)

            def _fmt_date(val: Any) -> str:
                if not val:
                    return ""
                if isinstance(val, datetime):
                    return val.strftime("%Y-%m-%d")
                s = str(val)
                if "T" in s:
                    return s.split("T")[0]
                return s[:10]

            paths = []
            for r in records:
                s_name = r.get("s_name") or ""
                t_name = r.get("t_name") or ""
                pred = r.get("predicate") or ""
                status = r.get("status") or "active"
                vf = _fmt_date(r.get("valid_from"))
                vu = _fmt_date(r.get("valid_until"))

                if status == "superseded" and vu:
                    annot = f"[status: superseded, valid_from: {vf}, valid_until: {vu}]"
                elif status == "superseded":
                    annot = f"[status: superseded, valid_from: {vf}]"
                else:
                    annot = f"[status: active, valid_from: {vf}]"

                paths.append({
                    "hops": 1,
                    "source_id": r.get("s_id"),
                    "source_name": s_name,
                    "target_id": r.get("t_id"),
                    "target_name": t_name,
                    "summary": f"{annot} {s_name} {pred} {t_name}",
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
                        "valid_from": r.get("valid_from"),
                        "valid_until": r.get("valid_until"),
                    }],
                })
            return paths
        except Exception as exc:
            logger.warning("Error fetching retrospective edges: %s", exc)
            return []


retrieval_service = RetrievalService()
