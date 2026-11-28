"""Entity resolution: exact auto-merging and cautious candidate linking for fuzzy matches."""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from pydantic import BaseModel, Field

from kryptodb.core.context import RequestContext
from kryptodb.core.ports import Embedder, EntityRecord, MemoryStore

logger = logging.getLogger("kryptodb.core.memory.entity_resolution")


class AliasCandidateContext(BaseModel):
    """Disambiguation context passed to an optional AliasArbiter."""

    name: str
    entity_type: Optional[str] = None
    summary: Optional[str] = None
    top_facts: List[str] = Field(default_factory=list)


class AliasCluster(BaseModel):
    """Resolved alias group returned by an AliasArbiter."""

    canonical_name: str
    aliases: List[str]
    entity_type: Optional[str] = None
    confidence: float = 1.0


class AliasClusterResult(BaseModel):
    """Strict Pydantic output schema for Block-then-LLM alias arbitration."""

    clusters: List[AliasCluster] = Field(default_factory=list)


AliasArbiter = Callable[[Optional[str], List[AliasCandidateContext]], AliasClusterResult]


@dataclass
class ResolutionResult:
    matched_entity: Optional[EntityRecord]
    is_exact_merge: bool
    possible_candidates: List[Tuple[EntityRecord, float]] = field(default_factory=list)
    is_unknown_entity: bool = False
    clarification_prompt: Optional[str] = None


def normalize_entity_name(name: str) -> str:
    """Normalizes entity name for deterministic exact comparison (casefold, trim, collapse whitespace)."""
    return " ".join(name.strip().casefold().split())


class EntityResolver:
    """Cautious entity resolution preventing silent identity collision.
    
    Guarantees:
    1. Auto-resolve ONLY on exact normalized name (casefold, trim, collapse whitespace)
       plus compatible entity type within the tenant.
    2. Near matches (edit distance, prefix, embedding similarity) must NEVER merge.
       Instead, creates a POSSIBLE_SAME_AS candidate and suggests clarification
       (e.g., 'Did you mean Harshit Kandpal?').
    3. Mentions with no node and no exact match are treated as unknown entities:
       facts must not be invented or guessed about them.
    """

    def __init__(
        self,
        fuzzy_threshold: float = 0.82,
        arbiter: Optional[AliasArbiter] = None,
    ):
        self.fuzzy_threshold = fuzzy_threshold
        self.arbiter = arbiter

    def arbitrate_aliases(
        self,
        candidates: List[AliasCandidateContext],
        entity_type: Optional[str] = None,
        arbiter: Optional[AliasArbiter] = None,
    ) -> AliasClusterResult:
        """Stage 2 Block-then-LLM alias arbitration with strict safety invariants.

        Invariants:
        1. Any candidate omitted by the arbiter falls back to a singleton cluster [name].
        2. Entities with incompatible types never merge; clusters below 0.90 confidence
           with mixed specificity/types are split back to singletons.
        """
        if not candidates:
            return AliasClusterResult(clusters=[])

        capped = candidates[:50]
        active_arbiter = arbiter or self.arbiter
        if active_arbiter is None:
            return AliasClusterResult(
                clusters=[
                    AliasCluster(
                        canonical_name=c.name,
                        aliases=[c.name],
                        entity_type=c.entity_type or entity_type,
                        confidence=1.0,
                    )
                    for c in capped
                ]
            )

        raw_result = active_arbiter(entity_type, capped)
        by_norm: dict[str, AliasCandidateContext] = {
            normalize_entity_name(c.name): c for c in capped
        }
        seen_norms: set[str] = set()
        safe_clusters: List[AliasCluster] = []

        for cluster in raw_result.clusters:
            member_names = list(dict.fromkeys(cluster.aliases or [cluster.canonical_name]))
            valid_members = [m for m in member_names if normalize_entity_name(m) in by_norm]
            if not valid_members:
                continue

            # Check type compatibility across cluster members
            member_types = {
                (by_norm[normalize_entity_name(m)].entity_type or "").strip().upper()
                for m in valid_members
                if by_norm[normalize_entity_name(m)].entity_type
            }
            if len(member_types) > 1 or (len(valid_members) > 1 and cluster.confidence < 0.90):
                # Safety invariant #2: split incompatible or low-confidence merges into singletons
                for m in valid_members:
                    nm = normalize_entity_name(m)
                    if nm not in seen_norms:
                        seen_norms.add(nm)
                        cand = by_norm[nm]
                        safe_clusters.append(
                            AliasCluster(
                                canonical_name=cand.name,
                                aliases=[cand.name],
                                entity_type=cand.entity_type or entity_type,
                                confidence=1.0,
                            )
                        )
                continue

            unseen_members = [m for m in valid_members if normalize_entity_name(m) not in seen_norms]
            if not unseen_members:
                continue
            for m in unseen_members:
                seen_norms.add(normalize_entity_name(m))

            canon = cluster.canonical_name if cluster.canonical_name in unseen_members else unseen_members[0]
            safe_clusters.append(
                AliasCluster(
                    canonical_name=canon,
                    aliases=unseen_members,
                    entity_type=cluster.entity_type or (next(iter(member_types), None) if member_types else entity_type),
                    confidence=cluster.confidence,
                )
            )

        # Safety invariant #1: every omitted candidate falls back to a singleton cluster
        for c in capped:
            nm = normalize_entity_name(c.name)
            if nm not in seen_norms:
                seen_norms.add(nm)
                safe_clusters.append(
                    AliasCluster(
                        canonical_name=c.name,
                        aliases=[c.name],
                        entity_type=c.entity_type or entity_type,
                        confidence=1.0,
                    )
                )

        return AliasClusterResult(clusters=safe_clusters)

    def resolve(
        self,
        candidate_name: str,
        context: RequestContext,
        store: MemoryStore,
        embedder: Optional[Embedder] = None,
        expected_type: Optional[str] = None,
    ) -> ResolutionResult:
        clean_name = candidate_name.strip()
        norm_name = normalize_entity_name(clean_name)
        if not norm_name:
            return ResolutionResult(
                matched_entity=None,
                is_exact_merge=False,
                possible_candidates=[],
                is_unknown_entity=True,
            )

        # 1. Exact match lookup (strict normalization only)
        exact = store.find_entity(norm_name, context) or store.find_entity(clean_name, context)
        if exact:
            # Check compatible entity type if specified
            ent_type = exact.metadata.get("type") or exact.metadata.get("kind")
            if expected_type and ent_type and ent_type.upper() != expected_type.upper():
                logger.info(
                    "Exact name match '%s' has incompatible type '%s' (expected '%s'). Refusing auto-merge.",
                    exact.name, ent_type, expected_type
                )
            else:
                return ResolutionResult(
                    matched_entity=exact,
                    is_exact_merge=True,
                    possible_candidates=[],
                    is_unknown_entity=False,
                )

        # 2. Near-match candidate discovery (Lexical string distance + Vector similarity)
        possible_candidates: List[Tuple[EntityRecord, float]] = []
        seen_cand_ids = set()

        # 2a. Embedding-based search
        if embedder:
            try:
                cand_vec = embedder.embed(clean_name)
                scored = store.search_vector(cand_vec, top_k=5, context=context)
                for item in scored:
                    if item.item_type == "entity" and item.score >= self.fuzzy_threshold:
                        ent = store.find_entity(item.text, context)
                        if ent and normalize_entity_name(ent.name) != norm_name and ent.id not in seen_cand_ids:
                            seen_cand_ids.add(ent.id)
                            possible_candidates.append((ent, float(item.score)))
            except Exception as exc:
                logger.debug("Vector search error in entity resolver: %s", exc)

        # 2b. Edit distance / Lexical similarity check against known tenant entities
        # Ensures that names like "Harshi" vs "Harshit" are caught as near matches without auto-merging
        all_entities = store.search_entities(norm_name, context, limit=50)

        for ent in all_entities:
            if ent.id in seen_cand_ids:
                continue
            ent_norm = normalize_entity_name(ent.name)
            if ent_norm == norm_name:
                continue
            ratio = difflib.SequenceMatcher(None, norm_name, ent_norm).ratio()
            # Also check prefix / substring similarity (e.g. "Harshi" is a prefix of "Harshit")
            is_prefix = (norm_name.startswith(ent_norm) or ent_norm.startswith(norm_name)) and min(len(norm_name), len(ent_norm)) >= 4
            if ratio >= 0.70 or is_prefix:
                score = max(ratio, 0.85 if is_prefix else 0.75)
                possible_candidates.append((ent, score))
                seen_cand_ids.add(ent.id)

        # Sort candidates descending by similarity score
        possible_candidates.sort(key=lambda x: x[1], reverse=True)

        clarification = None
        if possible_candidates:
            top_cand = possible_candidates[0][0]
            clarification = f"Did you mean {top_cand.name}?"

        return ResolutionResult(
            matched_entity=None,
            is_exact_merge=False,
            possible_candidates=possible_candidates,
            is_unknown_entity=True,
            clarification_prompt=clarification,
        )


entity_resolver = EntityResolver()
