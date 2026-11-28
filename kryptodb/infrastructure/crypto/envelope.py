"""AES-256-GCM envelope encryption and Section 5 Neo4j-writer integrity checks (v3 spec)."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kryptodb.infrastructure.crypto.blind_index import BlindIndex
from kryptodb.infrastructure.crypto.keyring import LocalKeyFileProvider


class DecryptionKeyError(RuntimeError):
    """Raised when a sealed record cannot be decrypted (missing kid or AEAD failure)."""


class IntegrityMismatchError(RuntimeError):
    """Raised when plaintext edge metadata disagrees with the authenticated encrypted payload."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii"))


class EnvelopeCipher:
    """Per-record random DEK + AES-256-GCM envelope encryption with version-bound AAD."""

    PREFIX = "kdb:v1"

    def __init__(self, key_provider: LocalKeyFileProvider) -> None:
        self.key_provider = key_provider

    @staticmethod
    def build_aad(scope: str, record_id: str, version: int) -> bytes:
        return f"kdb:v1:{scope}:{record_id}:{version}".encode("utf-8")

    def _seal_json(self, scope: str, record_id: str, version: int, payload: Dict[str, Any]) -> Dict[str, str]:
        bundle = self.key_provider.bundle
        kid = bundle.current_kid
        aad = self.build_aad(scope, record_id, version)

        dek = AESGCM.generate_key(bit_length=256)
        payload_nonce = os.urandom(12)
        plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ct = AESGCM(dek).encrypt(payload_nonce, plaintext, aad)

        wrap_nonce = os.urandom(12)
        wrapped = AESGCM(bundle.enc_key).encrypt(wrap_nonce, dek, aad)

        return {
            "kid": kid,
            "wrapped_dek": f"{self.PREFIX}:{kid}:{_b64e(wrap_nonce)}.{_b64e(wrapped)}",
            "ciphertext": f"{self.PREFIX}:{kid}:{_b64e(payload_nonce)}.{_b64e(ct)}",
        }

    def _parse_sealed(self, sealed: str) -> Tuple[str, bytes, bytes]:
        if not isinstance(sealed, str) or not sealed.startswith(f"{self.PREFIX}:"):
            raise DecryptionKeyError("Sealed value is missing valid kdb:v1 prefix")
        parts = sealed.split(":", 3)
        if len(parts) != 4:
            raise DecryptionKeyError("Malformed sealed envelope structure")
        _, _, kid, blob = parts
        if "." not in blob:
            raise DecryptionKeyError("Malformed sealed nonce/ciphertext blob")
        nonce_b64, ct_b64 = blob.split(".", 1)
        try:
            return kid, _b64d(nonce_b64), _b64d(ct_b64)
        except Exception as exc:
            raise DecryptionKeyError("Invalid base64 encoding in sealed envelope") from exc

    def _open_json(self, scope: str, record_id: str, version: int, ciphertext: str, wrapped_dek: str) -> Dict[str, Any]:
        kid_w, wrap_nonce, wrapped_bytes = self._parse_sealed(wrapped_dek)
        kid_c, payload_nonce, ct_bytes = self._parse_sealed(ciphertext)
        if kid_w != kid_c:
            raise IntegrityMismatchError(f"kid mismatch between wrapped_dek ({kid_w}) and ciphertext ({kid_c})")

        enc_key = self.key_provider.bundle.get_enc_key(kid_w)
        if enc_key is None:
            raise DecryptionKeyError(f"Missing encryption key for kid '{kid_w}'")

        aad = self.build_aad(scope, record_id, version)
        try:
            dek = AESGCM(enc_key).decrypt(wrap_nonce, wrapped_bytes, aad)
            plaintext = AESGCM(dek).decrypt(payload_nonce, ct_bytes, aad)
            decoded = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise DecryptionKeyError(f"AES-256-GCM decryption failed for record '{record_id}': {exc}") from exc

        if not isinstance(decoded, dict):
            raise IntegrityMismatchError("Decrypted payload is not a JSON object")
        return decoded

    def rewrap_dek(self, scope: str, record_id: str, version: int, wrapped_dek: str) -> Tuple[str, str]:
        """Re-wraps an existing DEK under current_kid without re-encrypting the payload."""
        old_kid, wrap_nonce, wrapped_bytes = self._parse_sealed(wrapped_dek)
        old_key = self.key_provider.bundle.get_enc_key(old_kid)
        if old_key is None:
            raise DecryptionKeyError(f"Cannot rewrap: missing encryption key for kid '{old_kid}'")
        aad = self.build_aad(scope, record_id, version)
        try:
            dek = AESGCM(old_key).decrypt(wrap_nonce, wrapped_bytes, aad)
        except Exception as exc:
            raise DecryptionKeyError(f"Cannot unwrap DEK for '{record_id}': {exc}") from exc

        new_kid = self.key_provider.bundle.current_kid
        new_nonce = os.urandom(12)
        new_wrapped = AESGCM(self.key_provider.bundle.enc_key).encrypt(new_nonce, dek, aad)
        return new_kid, f"{self.PREFIX}:{new_kid}:{_b64e(new_nonce)}.{_b64e(new_wrapped)}"

    def seal_entity_profile(self, scope: str, entity_id: str, profile_dict: Dict[str, Any]) -> Dict[str, str]:
        """Seal an entity profile using profile-specific authenticated associated data."""
        aad = f"kdb:v1:{scope}:{entity_id}:profile".encode("utf-8")
        bundle = self.key_provider.bundle
        kid = bundle.current_kid
        dek = AESGCM.generate_key(bit_length=256)
        payload_nonce = os.urandom(12)
        plaintext = json.dumps(profile_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(dek).encrypt(payload_nonce, plaintext, aad)
        wrap_nonce = os.urandom(12)
        wrapped = AESGCM(bundle.enc_key).encrypt(wrap_nonce, dek, aad)
        return {
            "profile_kid": kid,
            "profile_wrapped_dek": f"{self.PREFIX}:{kid}:{_b64e(wrap_nonce)}.{_b64e(wrapped)}",
            "profile_ct": f"{self.PREFIX}:{kid}:{_b64e(payload_nonce)}.{_b64e(ciphertext)}",
        }

    def open_entity_profile(
        self, scope: str, entity_id: str, sealed: Dict[str, str]
    ) -> Dict[str, Any]:
        """Open an entity profile, authenticating its scope and entity identity."""
        profile_ct = sealed.get("profile_ct")
        wrapped_dek = sealed.get("profile_wrapped_dek")
        if not profile_ct or not wrapped_dek:
            raise DecryptionKeyError("Entity profile is missing profile_ct or profile_wrapped_dek")
        kid_c, payload_nonce, ciphertext = self._parse_sealed(profile_ct)
        kid_w, wrap_nonce, wrapped = self._parse_sealed(wrapped_dek)
        if kid_c != kid_w or sealed.get("profile_kid", kid_c) != kid_c:
            raise IntegrityMismatchError("Entity profile kid does not match its sealed values")
        enc_key = self.key_provider.bundle.get_enc_key(kid_c)
        if enc_key is None:
            raise DecryptionKeyError(f"Missing encryption key for kid '{kid_c}'")
        aad = f"kdb:v1:{scope}:{entity_id}:profile".encode("utf-8")
        try:
            dek = AESGCM(enc_key).decrypt(wrap_nonce, wrapped, aad)
            plaintext = AESGCM(dek).decrypt(payload_nonce, ciphertext, aad)
            decoded = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise DecryptionKeyError(f"AES-256-GCM profile decryption failed for entity '{entity_id}': {exc}") from exc
        if not isinstance(decoded, dict):
            raise IntegrityMismatchError("Decrypted entity profile is not a JSON object")
        return decoded

    def seal_entity_name(self, scope: str, entity_id: str, name: str) -> Dict[str, str]:
        sealed = self._seal_json(scope, entity_id, version=1, payload={"name": name, "scope": scope})
        return {
            "kid": sealed["kid"],
            "name_ct": sealed["ciphertext"],
            "wrapped_dek": sealed["wrapped_dek"],
        }

    def open_entity_name(
        self,
        scope: str,
        entity_id: str,
        name_ct: str,
        wrapped_dek: str,
        stored_name_bidx: str,
        blind_index: BlindIndex,
    ) -> str:
        payload = self._open_json(scope, entity_id, version=1, ciphertext=name_ct, wrapped_dek=wrapped_dek)
        name = str(payload.get("name", ""))
        expected_bidx = blind_index.compute("entity", name)
        if expected_bidx != stored_name_bidx:
            raise IntegrityMismatchError(f"Entity '{entity_id}' name_bidx does not match decrypted name")
        return name

    def seal_fact(
        self,
        *,
        fact_id: str,
        scope: str,
        version: int,
        status: str,
        subject: str,
        predicate: str,
        obj: str,
        description: str,
        valid_from: str,
        valid_until: Optional[str],
        confidence: float,
        trust_weight: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        payload = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "description": description,
            "scope": scope,
            "version": int(version),
            "status": str(status),
            "valid_from": valid_from,
            "valid_until": valid_until,
            "confidence": float(confidence),
            "trust_weight": float(trust_weight),
            "metadata": metadata or {},
        }
        sealed = self._seal_json(scope, fact_id, version=int(version), payload=payload)
        return {
            "kid": sealed["kid"],
            "knowledge_ct": sealed["ciphertext"],
            "wrapped_dek": sealed["wrapped_dek"],
        }

    def open_and_verify_fact(
        self,
        edge_props: Dict[str, Any],
        blind_index: BlindIndex,
    ) -> Dict[str, Any]:
        """Decrypts a :FACT edge and enforces all Section 5 Neo4j-writer integrity invariants."""
        fact_id = str(edge_props.get("id", ""))
        scope = str(edge_props.get("scope", ""))
        version = int(edge_props.get("version", 1))
        status = str(edge_props.get("status", ""))
        knowledge_ct = edge_props.get("knowledge_ct")
        wrapped_dek = edge_props.get("wrapped_dek")

        if not knowledge_ct or not wrapped_dek:
            raise DecryptionKeyError(f"Fact '{fact_id}' is missing knowledge_ct or wrapped_dek")

        payload = self._open_json(scope, fact_id, version=version, ciphertext=knowledge_ct, wrapped_dek=wrapped_dek)

        # Section 5: verify version, status, and temporal/trust fields against authenticated payload
        if int(payload.get("version", -1)) != version:
            raise IntegrityMismatchError(f"Fact '{fact_id}' edge version tampered")
        if str(payload.get("status", "")) != status:
            raise IntegrityMismatchError(f"Fact '{fact_id}' edge status tampered ({status} != {payload.get('status')})")
        if edge_props.get("valid_from") is not None and str(payload.get("valid_from")) != str(edge_props.get("valid_from")):
            raise IntegrityMismatchError(f"Fact '{fact_id}' valid_from tampered")
        if (edge_props.get("valid_until") or None) != (payload.get("valid_until") or None):
            raise IntegrityMismatchError(f"Fact '{fact_id}' valid_until tampered")
        if edge_props.get("trust_weight") is not None and abs(float(edge_props["trust_weight"]) - float(payload["trust_weight"])) > 1e-6:
            raise IntegrityMismatchError(f"Fact '{fact_id}' trust_weight tampered")
        if edge_props.get("confidence") is not None and abs(float(edge_props["confidence"]) - float(payload["confidence"])) > 1e-6:
            raise IntegrityMismatchError(f"Fact '{fact_id}' confidence tampered")

        # Section 5: recompute blind indexes and search tokens and compare with stored edge properties
        subject = str(payload["subject"])
        predicate = str(payload["predicate"])
        obj = str(payload["object"])
        description = str(payload.get("description", ""))

        if blind_index.compute("subject", subject) != edge_props.get("subject_bidx"):
            raise IntegrityMismatchError(f"Fact '{fact_id}' subject_bidx mismatch")
        if blind_index.compute("predicate", predicate) != edge_props.get("predicate_bidx"):
            raise IntegrityMismatchError(f"Fact '{fact_id}' predicate_bidx mismatch")
        if blind_index.compute("object", obj) != edge_props.get("object_bidx"):
            raise IntegrityMismatchError(f"Fact '{fact_id}' object_bidx mismatch")

        expected_tokens = blind_index.extract_search_tokens(subject, predicate, obj, description)
        stored_tokens: List[str] = list(edge_props.get("search_tokens") or [])
        if expected_tokens != stored_tokens:
            raise IntegrityMismatchError(f"Fact '{fact_id}' search_tokens mismatch")

        return payload
