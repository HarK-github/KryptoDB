"""Unified configuration and settings module for KryptoDB GraphRAG Memory Substrate."""

from __future__ import annotations

import os
from pydantic import BaseModel, Field


class MemorySettings(BaseModel):
    """Centralized thresholds and flags governing turn analysis, write policy, and retrieval."""

    # Write & Validation Thresholds
    min_fact_confidence: float = Field(
        default=float(os.getenv("KRYPTODB_MIN_FACT_CONFIDENCE", "0.6")),
        description="Minimum confidence score required for a proposed fact to be committed or queued for approval.",
    )
    allow_subjective_predicates: bool = Field(
        default=os.getenv("KRYPTODB_ALLOW_SUBJECTIVE_PREDICATES", "false").lower() in ("true", "1", "yes"),
        description="Whether OPINION segments can be committed if matching subjective predicates in registry.",
    )
    require_quote_evidence: bool = Field(
        default=True,
        description="Strictly require that any proposed fact's quote is an exact verbatim substring of the user message.",
    )

    # Retrieval & Ranking Thresholds
    recall_relevance_cutoff: float = Field(
        default=float(os.getenv("KRYPTODB_RECALL_RELEVANCE_CUTOFF", "0.20")),
        description="Cross-encoder relevance score cutoff in [0, 1]. Items scoring below this are dropped.",
    )
    recall_max_facts: int = Field(
        default=int(os.getenv("KRYPTODB_RECALL_MAX_FACTS", "8")),
        description="Hard cap on number of deduplicated memories injected into the agent prompt context.",
    )
    max_recent_turns: int = Field(
        default=int(os.getenv("KRYPTODB_MAX_RECENT_TURNS", "5")),
        description="Number of conversation turns supplied for pronoun and coreference resolution.",
    )

    # Grounding & Integrity Checks
    grounding_check_mode: str = Field(
        default=os.getenv("KRYPTODB_GROUNDING_CHECK_MODE", "shadow"),
        description="Grounding check enforcement: 'disabled' | 'shadow' (log warnings) | 'enforce' (regenerate if ungrounded).",
    )
    debug_pipeline_logging: bool = Field(
        default=os.getenv("KRYPTODB_DEBUG_PIPELINE", "false").lower() in ("true", "1", "yes"),
        description="Structured debug logging across pipeline stages without logging raw user text at INFO.",
    )


settings = MemorySettings()
