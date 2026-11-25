"""Local key-file loader for KryptoDB v3 thin-slice encryption.

Loads separate 256-bit encryption (enc_key) and blind-index (index_key) keys
with opaque random key IDs (kid) and previous_kids support for DEK re-wrapping.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class KeyBundle:
    current_kid: str
    enc_key: bytes
    index_key: bytes
    previous_kids: Dict[str, bytes] = field(default_factory=dict)

    def get_enc_key(self, kid: str) -> Optional[bytes]:
        if kid == self.current_kid:
            return self.enc_key
        return self.previous_kids.get(kid)


def _decode_32b(b64_val: str, label: str) -> bytes:
    try:
        raw = base64.b64decode(b64_val, validate=True)
    except Exception as exc:
        raise ValueError(f"{label} must be valid base64") from exc
    if len(raw) != 32:
        raise ValueError(f"{label} must decode to exactly 32 bytes (256 bits)")
    return raw


def generate_key_file(path: str | Path) -> KeyBundle:
    """Generates a new local key file with distinct 256-bit enc_key and index_key and an opaque random kid."""
    kid = f"kid_{secrets.token_hex(6)}"
    enc_key = secrets.token_bytes(32)
    index_key = secrets.token_bytes(32)
    payload = {
        "current_kid": kid,
        "enc_key": base64.b64encode(enc_key).decode("ascii"),
        "index_key": base64.b64encode(index_key).decode("ascii"),
        "previous_kids": {},
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return KeyBundle(current_kid=kid, enc_key=enc_key, index_key=index_key, previous_kids={})


class LocalKeyFileProvider:
    """Loads and validates a v3 local key file (fail-closed)."""

    def __init__(self, key_file: Optional[str | Path] = None, bundle: Optional[KeyBundle] = None) -> None:
        if bundle is not None:
            self._validate_bundle(bundle)
            self._bundle = bundle
            return

        resolved = key_file or os.getenv("KRYPTODB_KEY_FILE")
        if not resolved:
            raise RuntimeError("KRYPTODB_KEY_FILE is required when encrypted storage mode is active.")
        self._bundle = self._load(Path(resolved))

    @staticmethod
    def _validate_bundle(bundle: KeyBundle) -> None:
        if not bundle.current_kid or not isinstance(bundle.current_kid, str):
            raise ValueError("current_kid must be a non-empty opaque string")
        if len(bundle.enc_key) != 32 or len(bundle.index_key) != 32:
            raise ValueError("enc_key and index_key must be 32 bytes each")
        if secrets.compare_digest(bundle.enc_key, bundle.index_key):
            raise ValueError("enc_key and index_key must be distinct keys")

    @classmethod
    def _load(cls, path: Path) -> KeyBundle:
        if not path.exists():
            raise RuntimeError(f"Key file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid JSON in key file: {path}") from exc
        if not isinstance(data, dict):
            raise ValueError("Key file root must be a JSON object")

        current_kid = data.get("current_kid")
        if not isinstance(current_kid, str) or not current_kid.strip():
            raise ValueError("Key file requires non-empty string 'current_kid'")
        enc_key = _decode_32b(str(data.get("enc_key", "")), "enc_key")
        index_key = _decode_32b(str(data.get("index_key", "")), "index_key")

        prev_raw = data.get("previous_kids") or {}
        if not isinstance(prev_raw, dict):
            raise ValueError("previous_kids must be an object mapping kid -> base64 key")
        previous_kids: Dict[str, bytes] = {
            str(k): _decode_32b(str(v), f"previous_kids[{k}]")
            for k, v in prev_raw.items()
        }

        bundle = KeyBundle(
            current_kid=current_kid.strip(),
            enc_key=enc_key,
            index_key=index_key,
            previous_kids=previous_kids,
        )
        cls._validate_bundle(bundle)
        return bundle

    @property
    def bundle(self) -> KeyBundle:
        return self._bundle

    def rotate_enc_key(self, new_enc_key: Optional[bytes] = None, new_kid: Optional[str] = None) -> KeyBundle:
        """Rotates enc_key in memory, preserving the previous key under previous_kids."""
        old = self._bundle
        next_key = new_enc_key or secrets.token_bytes(32)
        next_kid = new_kid or f"kid_{secrets.token_hex(6)}"
        prev = dict(old.previous_kids)
        prev[old.current_kid] = old.enc_key
        updated = KeyBundle(
            current_kid=next_kid,
            enc_key=next_key,
            index_key=old.index_key,
            previous_kids=prev,
        )
        self._validate_bundle(updated)
        self._bundle = updated
        return updated
