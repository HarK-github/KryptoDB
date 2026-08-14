"""Helpers for migrating legacy partition-encrypted graph facts.

The migration is intentionally split from the database loop: callers can run
it in a transaction, verify the envelope, and only then remove legacy fields.
"""

from __future__ import annotations

from synapse.integrity.partition_crypto import Encryptor


LEGACY_FIELDS_TO_REMOVE = (
    "predicate",
    "source_agent_id",
    "subject_id",
    "object_id",
)


def migrate_legacy_fact(
    encryptor: Encryptor,
    partition: str,
    edge_id: str,
    edge_props: dict,
    subject_props: dict,
    object_props: dict,
    session_keyring=None,
) -> dict:
    """Return encrypted replacement properties for one legacy fact.

    Raises ``ValueError`` if the legacy entity ciphertext cannot be opened.
    No database mutation occurs here.
    """
    subject_ct = subject_props.get("name_ct")
    object_ct = object_props.get("name_ct")
    subject = encryptor.decrypt(partition, subject_ct, session_keyring) if subject_ct else None
    object_value = encryptor.decrypt(partition, object_ct, session_keyring) if object_ct else None
    if subject is None or object_value is None:
        raise ValueError("legacy fact cannot be decrypted with the active partition key")

    envelope = {
        "subject": subject,
        "predicate": edge_props.get("predicate"),
        "description": edge_props.get("description"),
        "object": object_value,
        "source_agent_id": edge_props.get("source_agent_id"),
        "subject_tags": [],
        "object_tags": [],
        "derived_from": edge_props.get("derived_from") or [],
        "is_suspicious": bool(edge_props.get("is_suspicious", False)),
    }
    if not envelope["predicate"]:
        raise ValueError("legacy fact has no predicate")

    encrypted = encryptor.encrypt_knowledge(partition, edge_id, envelope)
    return {
        "knowledge_id": edge_id,
        **encrypted,
        "legacy_fields_to_remove": list(LEGACY_FIELDS_TO_REMOVE),
    }
