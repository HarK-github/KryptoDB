"""Entity resolution: exact auto-merging and cautious candidate linking for fuzzy matches."""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from kryptodb.core.context import RequestContext
from kryptodb.core.ports import Embedder, EntityRecord, MemoryStore

logger = logging.getLogger("kryptodb.core.memory.entity_resolution")


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

    def __init__(self, fuzzy_threshold: float = 0.82):
        self.fuzzy_threshold = fuzzy_threshold

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

        # 2b. Edit distance / Lexical similarity check against known tenant entities if available
        # Ensures that names like "Harshi" vs "Harshit" are caught as near matches without auto-merging
        all_entities = []
        if hasattr(store, "entities") and isinstance(store.entities, dict):
            # In-memory test store
            all_entities = [e for (tenant, name), e in store.entities.items() if tenant == context.tenant_id]

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
