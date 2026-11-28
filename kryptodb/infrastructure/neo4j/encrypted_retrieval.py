"""Phase 2 Encrypted Hybrid Retrieval Pipeline (Path A RAM Vector + Path B Blind Index + RRF)."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.models import MemoryRecord
from kryptodb.core.retrieval.provenance import ProvenanceStep, format_citation
from kryptodb.core.retrieval.ranking import AttributedMemoryResult
from kryptodb.infrastructure.crypto.blind_index import _tokenize_content, normalize
from kryptodb.infrastructure.crypto.envelope import DecryptionKeyError, IntegrityMismatchError
from kryptodb.infrastructure.crypto.ram_vector_index import RAMVectorIndex
from kryptodb.infrastructure.neo4j.encrypted_queries import (
    FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY,
    LIST_ALL_ACTIVE_ENCRYPTED_FACTS_QUERY,
    SEARCH_ENCRYPTED_BY_TOKENS_QUERY,
)
from kryptodb.infrastructure.neo4j.encrypted_store import EncryptedNeo4jMemoryStore

logger = logging.getLogger("kryptodb.infrastructure.neo4j.encrypted_retrieval")


class EncryptedRetrievalService:
    """Hybrid retrieval over EncryptedNeo4jMemoryStore + daemon-side RAMVectorIndex.

    Path A (Semantic): local query embedding -> RAMVectorIndex top-k fact_ids -> Neo4j 1-2 hop fetch.
    Path B (Exact/Token): entity + stopword-filtered word blind indexes -> Neo4j 1-2 hop fetch.
    Fused via RRF (k=60), decrypted with Section 5 integrity checks, verified, and reranked.
    """

    def __init__(
        self,
        store: EncryptedNeo4jMemoryStore,
        vector_index: RAMVectorIndex,
        embed_fn: Callable[[str], List[float]],
        rrf_k: int = 60,
        batch_failure_threshold: float = 0.05,
        alert_cooldown_seconds: float = 60.0,
    ) -> None:
        self.store = store
        self.vector_index = vector_index
        self.embed_fn = embed_fn
        self.rrf_k = rrf_k
        self.batch_failure_threshold = batch_failure_threshold
        self.alert_cooldown_seconds = alert_cooldown_seconds
        self._last_alert_ts: float = 0.0
        self.alert_count: int = 0

    def rebuild_index_for_scope(self, scope: str, context: RequestContext) -> Dict[str, Any]:
        """Streams and decrypts active facts from Neo4j to populate the RAM vector index at startup."""
        rows = self.store._run_query(
            LIST_ALL_ACTIVE_ENCRYPTED_FACTS_QUERY,
            scope=scope,
            now=context.now.isoformat(),
        )
        decrypted: List[MemoryRecord] = []
        for row in rows:
            decrypted.append(self.store._decrypt_edge(row["props"]))
        return self.vector_index.rebuild_from_facts(scope, decrypted, self.embed_fn)

    def _emit_rate_limited_alert(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_alert_ts >= self.alert_cooldown_seconds:
            self._last_alert_ts = now
            self.alert_count += 1
            logger.critical("ALERT [kryptodb_decrypt_failure]: %s", message)

    def _path_a_semantic(
        self,
        query: str,
        scope: str,
        now_iso: str,
        top_k: int,
        max_fanout: int,
    ) -> List[Tuple[Dict[str, Any], int]]:
        q_vec = self.embed_fn(query)
        seed_hits = self.vector_index.search(scope, q_vec, top_k=top_k)
        if not seed_hits:
            return []
        seed_ids = [fid for fid, _ in seed_hits]
        rows = self.store._run_query(
            FETCH_FACTS_BY_IDS_AND_NEIGHBORHOOD_QUERY,
            scope=scope,
            fact_ids=seed_ids,
            now=now_iso,
            max_results=max_fanout,
        )
        # Preserve seed ordering for hop-1, append hop-2 neighbors after
        seed_rank = {fid: idx for idx, fid in enumerate(seed_ids)}
        results: List[Tuple[Dict[str, Any], int]] = []
        for row in rows:
            props = row["props"]
            hops = int(row.get("hops", 1))
            results.append((props, hops))
        results.sort(key=lambda item: (item[1], seed_rank.get(str(item[0].get("id")), 9999)))
        return results

    def _path_b_blind_index(
        self,
        query: str,
        scope: str,
        now_iso: str,
        max_fanout: int,
    ) -> List[Tuple[Dict[str, Any], int]]:
        bidx = self.store.blind_index
        terms = _tokenize_content(query)
        entity_bidxs = [bidx.compute("entity", query)] + [bidx.compute("entity", t) for t in terms]
        word_tokens = [bidx.compute_token(t) for t in terms]
        if not entity_bidxs and not word_tokens:
            return []
        rows = self.store._run_query(
            SEARCH_ENCRYPTED_BY_TOKENS_QUERY,
            scope=scope,
            entity_bidxs=list(dict.fromkeys(entity_bidxs)),
            word_tokens=list(dict.fromkeys(word_tokens)),
            now=now_iso,
            max_results=max_fanout,
        )
        return [(row["props"], int(row.get("hops", 1))) for row in rows]

    def _search_scoped(
        self,
        query: str,
        context: RequestContext,
        scope: Optional[str] = None,
        top_k: int = 10,
        max_fanout: int = 50,
    ) -> List[AttributedMemoryResult]:
        if not query or not query.strip():
            return []

        target_scope = scope or (next(iter(context.active_scopes)) if context.active_scopes else "default")
        now_iso = context.now.isoformat()

        path_a = self._path_a_semantic(query, target_scope, now_iso, top_k=top_k * 2, max_fanout=max_fanout)
        path_b = self._path_b_blind_index(query, target_scope, now_iso, max_fanout=max_fanout)

        if not path_a and not path_b:
            return []

        # Reciprocal Rank Fusion (k=60) + deduplication by fact_id
        rrf_scores: Dict[str, float] = {}
        candidates_by_id: Dict[str, Tuple[Dict[str, Any], int, bool]] = {}

        for rank, (props, hops) in enumerate(path_a, start=1):
            fid = str(props.get("id", ""))
            if not fid:
                continue
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + (1.0 / (self.rrf_k + rank))
            if fid not in candidates_by_id or hops < candidates_by_id[fid][1]:
                candidates_by_id[fid] = (props, hops, False)

        for rank, (props, hops) in enumerate(path_b, start=1):
            fid = str(props.get("id", ""))
            if not fid:
                continue
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + (1.0 / (self.rrf_k + rank))
            if fid not in candidates_by_id:
                candidates_by_id[fid] = (props, hops, True)

        total_candidates = len(candidates_by_id)
        if total_candidates == 0:
            return []

        # Decrypt, enforce Section 5 integrity checks, and track decryption failure ratio
        query_terms = set(_tokenize_content(query))
        q_norm = normalize("entity", query)
        decrypted_items: List[Tuple[MemoryRecord, int, float]] = []
        decrypt_failures = 0

        for fid, (props, hops, from_path_b_only) in candidates_by_id.items():
            try:
                rec = self.store._decrypt_edge(props)
            except IntegrityMismatchError:
                # Reject tampered record without failing the whole batch
                continue
            except DecryptionKeyError as exc:
                decrypt_failures += 1
                if "Missing encryption key" in str(exc):
                    self._emit_rate_limited_alert(f"Missing kid during retrieval in scope '{target_scope}': {exc}")
                    raise
                continue

            # For Path-B-only 1-hop hits, verify exact normalized token/entity match (collision-safe)
            if from_path_b_only and hops == 1 and query_terms:
                rec_tokens = set(_tokenize_content(f"{rec.subject} {rec.predicate} {rec.object} {rec.description or ''}"))
                exact_entity = normalize("entity", rec.subject) == q_norm or normalize("entity", rec.object) == q_norm
                if not exact_entity and not (query_terms & rec_tokens):
                    continue

            decrypted_items.append((rec, hops, rrf_scores.get(fid, 0.0)))

        if decrypt_failures > 0 and (decrypt_failures / total_candidates) > self.batch_failure_threshold:
            msg = f"Batch decryption failure rate exceeded {self.batch_failure_threshold:.0%} ({decrypt_failures}/{total_candidates})"
            self._emit_rate_limited_alert(msg)
            raise DecryptionKeyError(msg)

        # Normalize RRF + combine with decrypted confidence/trust_weight & hop penalty
        max_rrf = max((item[2] for item in decrypted_items), default=1.0) or 1.0
        results: List[AttributedMemoryResult] = []
        for rec, hops, rrf_val in decrypted_items:
            rel_score = min(1.0, rrf_val / max_rrf)
            final_score = min(
                1.0,
                max(0.0, 0.65 * rel_score + 0.25 * rec.confidence + 0.10 * rec.trust_weight - 0.10 * (hops - 1)),
            )
            step = ProvenanceStep(
                hop=hops,
                source=rec.subject,
                predicate=rec.predicate,
                target=rec.object,
                edge_id=rec.id,
                confidence=rec.confidence,
                scope=rec.scope,
            )
            results.append(
                AttributedMemoryResult(
                    fact_id=rec.id,
                    summary=rec.description or f"{rec.subject} {rec.predicate} {rec.object}",
                    hops=hops,
                    final_score=round(final_score, 4),
                    relevance_score=round(rel_score, 4),
                    confidence=rec.confidence,
                    recency_score=1.0,
                    citation_tag=format_citation(context.tenant_id, [rec.scope], [rec.id]),
                    provenance_path=[step],
                )
            )

        results.sort(key=lambda r: r.final_score, reverse=True)
        return results[:top_k]

    # ----- core.ports.Retriever protocol conformance -----

    def retriever_search(
        self,
        query: str,
        context: RequestContext,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
    ) -> List[AttributedMemoryResult]:
        """Retriever protocol wrapper: fans out across all context scopes and deduplicates."""
        scopes = [s for s in list(context.active_scopes) if s and s != "*"] or ["default"]
        combined: List[AttributedMemoryResult] = []
        seen_ids: set = set()
        for sc in scopes:
            for hit in self._search_scoped(query=query, context=context, scope=sc, top_k=top_k):
                if hit.fact_id not in seen_ids:
                    seen_ids.add(hit.fact_id)
                    combined.append(hit)
        combined.sort(key=lambda r: r.final_score, reverse=True)
        return combined[:top_k]

    def search(
        self,
        query: str,
        context: RequestContext,
        top_k: int = 10,
        max_hops: int = 2,
        relevance_cutoff: Optional[float] = None,
        scope: Optional[str] = None,
        max_fanout: int = 50,
    ) -> List[AttributedMemoryResult]:
        """core.ports.Retriever protocol implementation (supports optional single-scope override)."""
        if scope is not None:
            return self._search_scoped(
                query=query,
                context=context,
                scope=scope,
                top_k=top_k,
                max_fanout=max_fanout,
            )
        return self.retriever_search(
            query=query,
            context=context,
            top_k=top_k,
            max_hops=max_hops,
            relevance_cutoff=relevance_cutoff,
        )
