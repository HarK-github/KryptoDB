"""Deterministic, sub-millisecond query parsing without LLM calls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

STOP_WORDS: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "and", "or", "to", "in", "on", "at",
    "by", "for", "with", "about", "against", "between", "into", "through", "during",
    "before", "after", "above", "below", "from", "up", "down", "out", "off", "over",
    "under", "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "what", "which", "who", "whom", "this",
    "that", "these", "those", "am", "been", "being", "have", "has", "had", "having",
    "do", "does", "did", "doing", "tell", "me", "find", "get", "show"
}

TEMPORAL_KEYWORDS: Set[str] = {
    "today", "yesterday", "tomorrow", "now", "recent", "recently", "current",
    "currently", "past", "history", "former", "formerly", "last", "latest",
    "active", "superseded", "revoked"
}


@dataclass(frozen=True)
class ParsedQuery:
    raw_query: str
    clean_text: str
    tokens: List[str]
    quoted_entities: List[str] = field(default_factory=list)
    temporal_hints: List[str] = field(default_factory=list)


def parse_query(raw_query: str) -> ParsedQuery:
    """Parses user or agent query string deterministically with sub-millisecond latency."""
    text = (raw_query or "").strip()
    if not text:
        return ParsedQuery(raw_query="", clean_text="", tokens=[])

    # Extract quoted terms e.g. "Alice" or 'Project Alpha'
    quoted = re.findall(r'["\']([^"\']+)["\']', text)

    # Tokenize and normalize
    raw_tokens = re.findall(r"\b[\w\-]+\b", text)
    tokens = [t.lower() for t in raw_tokens if t.lower() not in STOP_WORDS]

    temporal = [t.lower() for t in raw_tokens if t.lower() in TEMPORAL_KEYWORDS]

    return ParsedQuery(
        raw_query=text,
        clean_text=" ".join(tokens) if tokens else text,
        tokens=tokens,
        quoted_entities=quoted,
        temporal_hints=temporal,
    )
