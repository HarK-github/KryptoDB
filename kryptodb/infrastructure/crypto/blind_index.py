"""Canonical normalization and truncated HMAC-SHA256 blind indexing (v3 spec)."""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from typing import List

from kryptodb.core.memory.predicates import predicate_registry

STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "else", "of", "to", "in", "on", "at",
    "by", "for", "with", "about", "as", "into", "from", "up", "down", "out",
    "over", "under", "again", "this", "that", "these", "those", "it", "its",
    "he", "she", "they", "we", "you", "i", "me", "my", "your", "our", "their",
    "has", "have", "had", "do", "does", "did", "not", "no", "so", "than", "too",
    "very", "can", "will", "just", "should", "now", "what", "which", "who",
})

_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def normalize(kind: str, value: str) -> str:
    """Single canonical normalization contract used identically on writes and reads.

    Applies Unicode NFKC, strip, whitespace collapse, and casefold.
    Predicates also pass through PredicateRegistry.normalize().
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    collapsed = " ".join(text.strip().split())
    if kind == "predicate":
        return predicate_registry.normalize(collapsed).casefold()
    return collapsed.casefold()


def _tokenize_content(text: str) -> List[str]:
    norm = normalize("tok", text)
    cleaned = _NON_WORD_RE.sub(" ", norm)
    tokens = [t for t in cleaned.split() if len(t) > 1 and t not in STOP_WORDS]
    return list(dict.fromkeys(tokens))


class BlindIndex:
    """Keyed, domain-separated, truncated HMAC-SHA256 blind index generator."""

    def __init__(
        self,
        index_key: bytes,
        field_hex_len: int = 24,
        token_hex_len: int = 16,
        max_tokens: int = 12,
    ) -> None:
        if len(index_key) != 32:
            raise ValueError("index_key must be 32 bytes")
        if not (2 <= field_hex_len <= 64) or not (2 <= token_hex_len <= 64):
            raise ValueError("hex truncation lengths must be between 2 and 64")
        self._index_key = index_key
        self.field_hex_len = field_hex_len
        self.token_hex_len = token_hex_len
        self.max_tokens = max_tokens

    def compute(self, kind: str, value: str, hex_len: int | None = None) -> str:
        norm = normalize(kind, value)
        msg = f"{kind}:{norm}".encode("utf-8")
        digest = hmac.new(self._index_key, msg, hashlib.sha256).hexdigest()
        length = hex_len if hex_len is not None else self.field_hex_len
        return digest[:length]

    def compute_token(self, token: str) -> str:
        return self.compute("tok", token, hex_len=self.token_hex_len)

    def extract_search_tokens(
        self,
        subject: str,
        predicate: str,
        obj: str,
        description: str = "",
    ) -> List[str]:
        """Builds up to max_tokens (12) HMAC'd search tokens in deterministic priority order:
        1. Subject tokens
        2. Object tokens
        3. Normalized predicate token
        4. Longest remaining description tokens (ties broken alphabetically)
        """
        selected: List[str] = []
        seen: set[str] = set()

        def _add(tok: str) -> None:
            if len(selected) >= self.max_tokens:
                return
            if tok and tok not in seen:
                seen.add(tok)
                selected.append(tok)

        for t in _tokenize_content(subject):
            _add(t)
        for t in _tokenize_content(obj):
            _add(t)
        pred_norm = normalize("predicate", predicate)
        if pred_norm:
            _add(pred_norm)

        desc_tokens = [t for t in _tokenize_content(description) if t not in seen]
        desc_tokens.sort(key=lambda t: (-len(t), t))
        for t in desc_tokens:
            _add(t)

        return [self.compute_token(t) for t in selected]

    def compute_idempotency_key(
        self,
        scope: str,
        subject: str,
        predicate: str,
        obj: str,
        source_turn_id: str = "",
    ) -> str:
        msg = (
            f"idem:{normalize('scope', scope)}:"
            f"{normalize('subject', subject)}:"
            f"{normalize('predicate', predicate)}:"
            f"{normalize('object', obj)}:"
            f"{normalize('turn', source_turn_id)}"
        ).encode("utf-8")
        return hmac.new(self._index_key, msg, hashlib.sha256).hexdigest()[:32]
