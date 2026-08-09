import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from synapse.daemon.keyring import keyring
from synapse.storage.redis_client import redis_client

class Encryptor:
    def _get_or_create_key(self, partition: str) -> bytes:
        stored = redis_client.client.get(f"key:{partition}")
        if stored:
            return bytes.fromhex(stored)
            
        new_key = AESGCM.generate_key(bit_length=256)
        redis_client.client.set(f"key:{partition}", new_key.hex())
        return new_key
        
    def encrypt(self, partition: str, plaintext: str) -> str:
        key = self._get_or_create_key(partition)
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, plaintext.encode(), b"")
        return nonce.hex() + ":" + ct.hex()
        
    def decrypt(self, partition: str, ciphertext_bundle: str) -> str | None:
        key = keyring.get_key(partition)
        if not key:
            return None
            
        aesgcm = AESGCM(key)
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
