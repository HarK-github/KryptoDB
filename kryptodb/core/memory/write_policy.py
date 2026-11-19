"""Write policy and propose_memory payload validation.

Enforces write-time integrity, provenance quotation substring verification,
predicate registry membership, placeholder rejection, and source event authenticity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

from kryptodb.core.context import RequestContext
from kryptodb.core.memory.entity_resolution import normalize_entity_name
from kryptodb.core.memory.predicates import predicate_registry
from kryptodb.core.ports import Event, EventStore
from kryptodb.core.settings import settings

logger = logging.getLogger("kryptodb.core.memory.write_policy")


class WritePolicyViolation(ValueError):
    """Raised when a proposed fact violates write policy or provenance quotation rules."""
    pass


# Disallowed placeholder objects that represent failed extractions or template tokens
DISALLOWED_OBJECTS: Set[str] = {
    "object",
    "unknown",
    "none",
    "n/a",
    "na",
    "null",
    "value",
    "placeholder",
    "{}",
    "[]",
    "undefined",
    "something",
    "someone",
    "thing",
}

# Vague or uninformative predicates that should never be written to memory
VAGUE_PREDICATES: Set[str] = {
    "PERSON_KNOWS",
    "PERSON KNOWS",
    "KNOWS",
    "RELATED_TO",
    "FACT",
    "HAS_FACT",
    "PROCESSED_QUERY",
    "QUERY",
    "PROCESSED_MESSAGE",
    "ASSOCIATED_WITH",
}

# Sensitive predicates requiring human confirmation if write scope is restricted
SENSITIVE_PREDICATES: Set[str] = {
    "HAS_PASSWORD",
    "HAS_SSN",
    "CREDIT_CARD",
    "MEDICAL_CONDITION",
    "BIOMETRIC_DATA",
}


def validate_proposal(
    subject: Any,
    predicate: Any = None,
    obj: Any = None,
    confidence: float = 1.0,
    context: Optional[RequestContext] = None,
    source_event_id: Optional[str] = None,
    quote_substring: Optional[str] = None,
    event_store: Optional[EventStore] = None,
    session_id: Optional[str] = None,
    source_event: Optional[Any] = None,
    is_hypothetical: bool = False,
    source_message_role: Optional[str] = None,
    full_turn_text: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Enforces write-time integrity on memory proposals."""
    if isinstance(subject, dict):
        d = subject
        s = d.get("subject", "")
        p = d.get("predicate", "")
        o = d.get("obj", "") or d.get("object", "")
        confidence = float(d.get("confidence", confidence))
        quote_substring = d.get("quote", quote_substring)
        is_hypothetical = d.get("is_hypothetical", is_hypothetical)
    else:
        s = str(subject or "")
        p = str(predicate or "")
        o = str(obj or "")

    # 1. Non-empty string checks
    if not s or not s.strip():
        raise WritePolicyViolation("Subject must be a non-empty string.")
    if not p or not p.strip():
        raise WritePolicyViolation("Predicate must be a non-empty string.")
    if not o or not o.strip():
        raise WritePolicyViolation("Object must be a non-empty string.")

    clean_subj = s.strip()
    clean_pred = p.strip()
    clean_obj = o.strip()

    # 2. Placeholder object rejection
    if clean_obj.lower() in DISALLOWED_OBJECTS:
        raise WritePolicyViolation(
            f"Rejected placeholder object '{o}': placeholders like Object, Unknown, None, null, or value are prohibited."
        )

    # 3. Subject-Object identity rejection
    if normalize_entity_name(clean_subj) == normalize_entity_name(clean_obj):
        raise WritePolicyViolation(
            f"Subject and object cannot be identical ('{clean_subj}'). A fact must link distinct entities or attributes."
        )

    # 4. Predicate Registry & Vague Predicate Validation
    norm_pred = predicate_registry.normalize(clean_pred)
    if norm_pred in VAGUE_PREDICATES or clean_pred.upper() in VAGUE_PREDICATES:
        raise WritePolicyViolation(
            f"Rejected vague predicate '{p}'. Predicates must be specific (e.g. LIKES, LIVES_IN, WORKS_FOR)."
        )

    from kryptodb.core.memory.predicates import Cardinality
    if not predicate_registry.is_registered(norm_pred):
        # Dynamic Schema Evolution: Allow the LLM to decide novel predicates dynamically
        predicate_registry.register(norm_pred, cardinality=Cardinality.MANY)
        logger.info("Dynamic Schema Evolution: Auto-registered novel predicate '%s' with Cardinality.MANY", norm_pred)

    # 5. Confidence Threshold
    if confidence < 0.0 or confidence > 1.0:
        raise WritePolicyViolation(f"Confidence must be in [0.0, 1.0], got {confidence}")

    if confidence < settings.min_fact_confidence:
        raise WritePolicyViolation(
            f"Fact confidence {confidence:.2f} is below minimum threshold ({settings.min_fact_confidence:.2f})."
        )

    # 6. Hypothetical / Hedged statement restriction
    if is_hypothetical:
        raise WritePolicyViolation(
            "Hypothetical or speculative statements ('might', 'maybe', 'thinking of') cannot be committed as durable facts."
        )

    # 7. Source authenticity: Source must be a USER event (reject assistant text)
    if source_message_role and source_message_role.lower() in ("assistant", "agent", "ai", "system"):
        raise WritePolicyViolation(
            f"Fact source must be a user event. Rejected fact derived from {source_message_role} text."
        )

    matching_event = source_event
    sess_id = session_id or (context.session_id if context else None)

    if not matching_event and source_event_id and event_store and sess_id:
        events = event_store.list_by_session(sess_id, context)
        matching_event = next((e for e in events if e.id == source_event_id), None)
        if not matching_event:
            raise WritePolicyViolation(
                f"source_event_id '{source_event_id}' not found in session '{sess_id}'."
            )

    if matching_event:
        # Check event role / type
        role = getattr(matching_event, "role", None) or getattr(matching_event, "type", None)
        if hasattr(matching_event, "metadata") and isinstance(matching_event.metadata, dict):
            role = matching_event.metadata.get("role", role)

        if role in ("assistant", "agent", "ai", "system"):
            raise WritePolicyViolation(
                f"Fact source must be a user event. Rejected fact derived from {role} text."
            )

    # 8. Quote substring validation
    if quote_substring is not None:
        if full_turn_text:
            if quote_substring.strip() not in full_turn_text:
                raise WritePolicyViolation(
                    f"quote not found in source message: '{quote_substring}' is not a verbatim substring of source event."
                )
        elif matching_event:
            content = getattr(matching_event, "content", "")
            if quote_substring.strip() not in content:
                raise WritePolicyViolation(
                    f"quote not found in source message: '{quote_substring}' is not a verbatim substring of source event."
                )
        elif source_event_id and event_store and sess_id:
            raise WritePolicyViolation("quote_substring provided without valid source event.")
    elif settings.require_quote_evidence and matching_event:
        content = getattr(matching_event, "content", "")
        # Require that either the object or the statement is grounded in the event content
        if clean_obj.lower() not in content.lower() and clean_subj.lower() not in content.lower():
            raise WritePolicyViolation(
                f"Fact evidence missing: neither subject '{clean_subj}' nor object '{clean_obj}' appears in source message."
            )
