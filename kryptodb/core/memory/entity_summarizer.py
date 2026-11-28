"""Event-driven Hub Entity Summarizer (encrypt-compatible)."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field


class EntityProfile(BaseModel):
    """Structured summary profile for a high-degree hub entity."""

    entity_id: str
    summary: str
    time_range_start: Optional[str] = None
    time_range_end: Optional[str] = None
    key_facts: List[str] = Field(default_factory=list)
    source_fact_ids: List[str] = Field(default_factory=list)


class EntitySummarizer:
    """Event-driven summarizer triggered when incident :FACT edges change.

    Only generates/updates a profile when degree >= KRYPTODB_HUB_SUMMARY_MIN_DEGREE
    (default 3). In encrypted mode, seals the EntityProfile JSON via EnvelopeCipher
    with AAD = 'kdb:v1:{scope}:{entity_id}:profile' so zero plaintext summary
    properties are stored on :Entity nodes.
    """

    VALID_EVENTS = {"commit", "supersede", "revoke"}

    def __init__(
        self,
        min_degree: Optional[int] = None,
        summarize_fn: Optional[Callable[[str, List[Dict[str, Any]]], str]] = None,
        cipher: Optional[Any] = None,
        storage_mode: Optional[str] = None,
    ) -> None:
        self.min_degree = (
            min_degree
            if min_degree is not None
            else int(os.getenv("KRYPTODB_HUB_SUMMARY_MIN_DEGREE", "3"))
        )
        self.summarize_fn = summarize_fn
        self.cipher = cipher
        self.storage_mode = (
            storage_mode
            if storage_mode is not None
            else os.getenv("KRYPTODB_STORAGE_MODE", "plaintext").strip().lower()
        )

    def on_fact_change(
        self,
        entity_id: str,
        scope: str,
        incident_facts: List[Dict[str, Any]],
        event_type: str = "commit",
    ) -> Optional[Dict[str, Any]]:
        """Recomputes hub EntityProfile on commit/supersede/revoke if degree >= min_degree."""
        if event_type not in self.VALID_EVENTS:
            raise ValueError(f"Unsupported event_type '{event_type}'; expected one of {sorted(self.VALID_EVENTS)}")

        active_facts = [
            f for f in incident_facts
            if f.get("status", "active") == "active"
        ]
        if len(active_facts) < self.min_degree:
            return None

        timestamps = sorted(
            str(f["valid_from"])
            for f in active_facts
            if f.get("valid_from")
        )
        key_facts = [
            f.get("description") or f"{f.get('subject', entity_id)} {f.get('predicate', 'RELATED_TO')} {f.get('object', '')}".strip()
            for f in active_facts
        ]
        source_fact_ids = [str(f["id"]) for f in active_facts if f.get("id")]

        if self.summarize_fn is not None:
            summary_text = self.summarize_fn(entity_id, active_facts)
        else:
            summary_text = f"{entity_id}: " + "; ".join(key_facts[:5])

        profile = EntityProfile(
            entity_id=entity_id,
            summary=summary_text,
            time_range_start=timestamps[0] if timestamps else None,
            time_range_end=timestamps[-1] if timestamps else None,
            key_facts=key_facts,
            source_fact_ids=source_fact_ids,
        )

        if self.storage_mode == "encrypted":
            if self.cipher is None:
                raise RuntimeError(
                    "EntitySummarizer in encrypted mode requires an EnvelopeCipher instance (cipher=)."
                )
            aad = f"kdb:v1:{scope}:{entity_id}:profile"
            sealed = self.cipher.seal_entity_profile(scope, entity_id, profile.model_dump())
            return {
                "mode": "encrypted",
                "entity_id": entity_id,
                "aad": aad,
                "sealed": sealed,
            }

        return {
            "mode": "plaintext",
            "entity_id": entity_id,
            "profile": profile,
        }
