import os
import hmac
import hashlib
import json
import base64
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from kryptodb.daemon.keyring import keyring
from kryptodb.storage.redis_client import redis_client
from dotenv import load_dotenv

load_dotenv()

_session_blind_idx_cache = {}

def clear_crypto_cache():
    _session_blind_idx_cache.clear()

class Encryptor:
    def __init__(self):
        mk_hex = os.environ.get("KRYPTODB_MASTER_KEY")
        if not mk_hex:
            if os.environ.get("KRYPTODB_ENV", "development").lower() == "production":
                raise RuntimeError("KRYPTODB_MASTER_KEY is required in production")
            # Development-only compatibility for the existing local test setup.
            # Production explicitly fails closed above.
            mk_hex = "d3f4u1t_m4st3r_k3y_32_byt3s_l0ng!!".encode().hex()
        if len(mk_hex) != 64:
            raise ValueError("KRYPTODB_MASTER_KEY must be exactly 32 bytes encoded as 64 hex characters")
        try:
            bytes.fromhex(mk_hex)
        except ValueError as exc:
            raise ValueError("KRYPTODB_MASTER_KEY must contain only hexadecimal characters") from exc
        self.master_key = bytes.fromhex(mk_hex)
        self.master_aes = AESGCM(self.master_key)
        self.scope_keys = self._load_customer_scope_keys()
        self.customer_keys_required = bool(self.scope_keys) or os.environ.get("KRYPTODB_ENV", "development").lower() == "production"
        self._memory_keys: dict[str, bytes] = {}

    @staticmethod
    def _load_customer_scope_keys() -> dict[str, tuple[str, bytes]]:
        """Load customer-owned scope KEKs without persisting or logging them."""
        raw = os.environ.get("KRYPTODB_SCOPE_KEYS_JSON")
        key_file = os.environ.get("KRYPTODB_SCOPE_KEYS_FILE")
        if key_file:
            try:
                raw = Path(key_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError("KRYPTODB_SCOPE_KEYS_FILE could not be read") from exc
        if not raw:
            return {}
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("KRYPTODB_SCOPE_KEYS_JSON must contain valid JSON") from exc
        if not isinstance(config, dict):
            raise ValueError("Scope key configuration must be an object")
        loaded: dict[str, tuple[str, bytes]] = {}
        for scope, entry in config.items():
            if not isinstance(scope, str) or not scope.strip() or not isinstance(entry, dict):
                raise ValueError("Each scope key entry must be an object")
            key_id = entry.get("key_id")
            encoded = entry.get("key")
            if not isinstance(key_id, str) or not key_id or not isinstance(encoded, str):
                raise ValueError(f"Scope {scope!r} requires key_id and key")
            try:
                key = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError(f"Scope {scope!r} key must be base64") from exc
            if len(key) != 32:
                raise ValueError(f"Scope {scope!r} key must decode to exactly 32 bytes")
            if scope in loaded:
                raise ValueError(f"Duplicate scope key: {scope}")
            loaded[scope] = (key_id, key)
        return loaded

    def scope_key_status(self) -> list[dict[str, str | bool]]:
        configured = set(self.scope_keys)
        scopes = configured
        try:
            from kryptodb.memory_api.agents import get_all_scopes
            scopes |= set(get_all_scopes())
        except Exception:
            pass
        result = []
        for scope in sorted(scopes):
            entry = self.scope_keys.get(scope)
            key = entry[1] if entry else None
            result.append({
                "scope": scope,
                "key_id": entry[0] if entry else None,
                "configured": bool(entry),
                "fingerprint": hashlib.sha256(key).hexdigest()[:16] if key else None,
            })
        return result

    def _scope_key_metadata(self, partition: str) -> tuple[str, bytes]:
        configured = self.scope_keys.get(partition)
        if configured:
            return configured
        if self.customer_keys_required:
            raise RuntimeError(f"Customer scope key is not configured for: {partition}")
        return "internal:development", self._get_internal_key(partition)

    def _get_internal_key(self, partition: str) -> bytes:
        if redis_client.client is None:
            if not hasattr(self, "_memory_keys"):
                self._memory_keys = {}
            if partition in self._memory_keys:
                return self._memory_keys[partition]
            new_key = AESGCM.generate_key(bit_length=256)
            self._memory_keys[partition] = new_key
            return new_key

        stored = redis_client.client.get(f"key:{partition}")
        if stored:
            if isinstance(stored, bytes):
                stored = stored.decode('ascii')
            parts = stored.split(":")
            if len(parts) == 2:
                nonce = bytes.fromhex(parts[0])
                ct = bytes.fromhex(parts[1])
                try:
                    return self.master_aes.decrypt(nonce, ct, b"")
                except Exception as exc:
                    raise RuntimeError("Failed to unwrap partition key. Master key may be incorrect.") from exc
            return bytes.fromhex(stored)
        new_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ct = self.master_aes.encrypt(nonce, new_key, b"")
        redis_client.client.set(f"key:{partition}", f"{nonce.hex()}:{ct.hex()}")
        return new_key

    def _get_or_create_key(self, partition: str) -> bytes:
        return self._scope_key_metadata(partition)[1]

    def _derive_keys(self, base_key: bytes) -> tuple[bytes, bytes]:
        hkdf_content = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"content-encryption")
        content_key = hkdf_content.derive(base_key)
        
        hkdf_blind = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"blind-index")
        blind_key = hkdf_blind.derive(base_key)
        return content_key, blind_key

    def _derive_search_key(self, base_key: bytes) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"search-index-v1",
        ).derive(base_key)

    @staticmethod
    def _search_message(field: str, plaintext: str) -> bytes:
        normalized = " ".join(str(plaintext).casefold().split())
        return f"{field}:{normalized}".encode("utf-8")

    @staticmethod
    def _search_terms(value: str) -> set[str]:
        normalized = " ".join(str(value).casefold().split())
        return {normalized, *[term for term in normalized.split(" ") if term]}

    def compute_search_idx(self, partition: str, field: str, plaintext: str) -> str:
        base_key = self._get_or_create_key(partition)
        search_key = self._derive_search_key(base_key)
        return hmac.new(search_key, self._search_message(field, plaintext), hashlib.sha256).hexdigest()

    def compute_search_idx_session(self, partition: str, field: str, plaintext: str, session_keyring=None) -> str | None:
        active_keyring = session_keyring or keyring
        base_key = active_keyring.get_key(partition)
        if not base_key:
            return None
        search_key = self._derive_search_key(base_key)
        return hmac.new(search_key, self._search_message(field, plaintext), hashlib.sha256).hexdigest()
        
    def encrypt(self, partition: str, plaintext: str) -> str:
        base_key = self._get_or_create_key(partition)
        content_key, _ = self._derive_keys(base_key)
        aesgcm = AESGCM(content_key)
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, plaintext.encode(), b"")
        return nonce.hex() + ":" + ct.hex()

    def encrypt_knowledge(self, partition: str, knowledge_id: str, envelope: dict) -> dict:
        """Encrypt one complete knowledge envelope with a unique DEK.

        The partition key is only a KEK here. It wraps the per-record DEK;
        it is never used directly to encrypt the knowledge payload.
        """
        key_id, kek = self._scope_key_metadata(partition)
        dek = AESGCM.generate_key(bit_length=256)
        aad = f"knowledge:{partition}:{knowledge_id}:v1".encode("utf-8")

        payload_nonce = os.urandom(12)
        payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(dek).encrypt(payload_nonce, payload, aad)

        wrap_nonce = os.urandom(12)
        wrapped_dek = AESGCM(kek).encrypt(wrap_nonce, dek, aad)
        return {
            "knowledge_ct": ciphertext.hex(),
            "knowledge_nonce": payload_nonce.hex(),
            "wrapped_dek": wrap_nonce.hex() + ":" + wrapped_dek.hex(),
            "knowledge_kek_version": "partition-v1",
            "knowledge_kek_id": key_id,
            "subject_search_idx": self.compute_search_idx(partition, "subject", envelope["subject"]) if "subject" in envelope else None,
            "predicate_search_idx": self.compute_search_idx(partition, "predicate", envelope["predicate"]) if "predicate" in envelope else None,
            "object_search_idx": self.compute_search_idx(partition, "object", envelope["object"]) if "object" in envelope else None,
            "search_indexes": [
                self.compute_search_idx(partition, field, term)
                for field in ("subject", "predicate", "object")
                if field in envelope
                for term in self._search_terms(envelope[field])
            ],
        }

    def decrypt_knowledge(self, partition: str, knowledge_id: str, record: dict, session_keyring=None) -> dict | None:
        active_keyring = session_keyring or keyring
        kek = active_keyring.get_key(partition)
        if not kek:
            return None
        try:
            aad = f"knowledge:{partition}:{knowledge_id}:v1".encode("utf-8")
            wrap_parts = record["wrapped_dek"].split(":")
            if len(wrap_parts) != 2:
                return None
            dek = AESGCM(kek).decrypt(bytes.fromhex(wrap_parts[0]), bytes.fromhex(wrap_parts[1]), aad)
            payload = AESGCM(dek).decrypt(
                bytes.fromhex(record["knowledge_nonce"]),
                bytes.fromhex(record["knowledge_ct"]),
                aad,
            )
            decoded = json.loads(payload.decode("utf-8"))
            return decoded if isinstance(decoded, dict) else None
        except Exception:
            return None
        
    def decrypt(self, partition: str, ciphertext_bundle: str, session_keyring=None) -> str | None:
        active_keyring = session_keyring or keyring
        base_key = active_keyring.get_key(partition)
        if not base_key:
            return None
            
        content_key, _ = self._derive_keys(base_key)
        aesgcm = AESGCM(content_key)
        parts = ciphertext_bundle.split(":")
        if len(parts) != 2:
            return None
        nonce = bytes.fromhex(parts[0])
        ct = bytes.fromhex(parts[1])
        try:
            pt = aesgcm.decrypt(nonce, ct, b"")
            return pt.decode()
        except Exception:
            return None

    def compute_blind_idx(self, partition: str, plaintext: str) -> str:
        base_key = self._get_or_create_key(partition)
        _, blind_key = self._derive_keys(base_key)
        h = hmac.new(blind_key, plaintext.encode(), hashlib.sha256)
        return h.hexdigest()

    def compute_blind_idx_session(self, partition: str, plaintext: str, session_keyring=None) -> str | None:
        cache_key = f"{partition}:{plaintext}"
        if cache_key in _session_blind_idx_cache:
            return _session_blind_idx_cache[cache_key]
            
        active_keyring = session_keyring or keyring
        base_key = active_keyring.get_key(partition)
        if not base_key:
            return None
        _, blind_key = self._derive_keys(base_key)
        h = hmac.new(blind_key, plaintext.encode(), hashlib.sha256)
        res = h.hexdigest()
        _session_blind_idx_cache[cache_key] = res
        return res
