import os
import hmac
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from synapse.daemon.keyring import keyring
from synapse.storage.redis_client import redis_client
from dotenv import load_dotenv

load_dotenv()

_session_blind_idx_cache = {}

def clear_crypto_cache():
    _session_blind_idx_cache.clear()

class Encryptor:
    def __init__(self):
        mk_hex = os.environ.get("SYNAPSE_MASTER_KEY", "d3f4u1t_m4st3r_k3y_32_byt3s_l0ng!!".encode().hex())
        if len(mk_hex) != 64:
            mk_hex = hashlib.sha256(mk_hex.encode()).hexdigest()
        self.master_key = bytes.fromhex(mk_hex)
        self.master_aes = AESGCM(self.master_key)

    def _get_or_create_key(self, partition: str) -> bytes:
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
                except Exception as e:
                    raise RuntimeError("Failed to unwrap partition key. Master key may be incorrect.") from e
            return bytes.fromhex(stored)
            
        new_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ct = self.master_aes.encrypt(nonce, new_key, b"")
        wrapped = f"{nonce.hex()}:{ct.hex()}"
        
        redis_client.client.set(f"key:{partition}", wrapped)
        return new_key

    def _derive_keys(self, base_key: bytes) -> tuple[bytes, bytes]:
        hkdf_content = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"content-encryption")
        content_key = hkdf_content.derive(base_key)
        
        hkdf_blind = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"blind-index")
        blind_key = hkdf_blind.derive(base_key)
        return content_key, blind_key
        
    def encrypt(self, partition: str, plaintext: str) -> str:
        base_key = self._get_or_create_key(partition)
        content_key, _ = self._derive_keys(base_key)
        aesgcm = AESGCM(content_key)
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, plaintext.encode(), b"")
        return nonce.hex() + ":" + ct.hex()
        
    def decrypt(self, partition: str, ciphertext_bundle: str) -> str | None:
        base_key = keyring.get_key(partition)
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

    def compute_blind_idx_session(self, partition: str, plaintext: str) -> str | None:
        cache_key = f"{partition}:{plaintext}"
        if cache_key in _session_blind_idx_cache:
            return _session_blind_idx_cache[cache_key]
            
        base_key = keyring.get_key(partition)
        if not base_key:
            return None
        _, blind_key = self._derive_keys(base_key)
        h = hmac.new(blind_key, plaintext.encode(), hashlib.sha256)
        res = h.hexdigest()
        _session_blind_idx_cache[cache_key] = res
        return res
