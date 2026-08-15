import pytest
from kryptodb.integrity.partition_crypto import Encryptor
from kryptodb.daemon.keyring import keyring

def test_inactive_partition_returns_ciphertext(monkeypatch):
    class MockRedisClient:
        def __init__(self):
            self.store = {}
        def get(self, key):
            return self.store.get(key)
        def set(self, key, value):
            self.store[key] = value
            
    class MockRedisWrapper:
        def __init__(self):
            self.client = MockRedisClient()

    mock_redis_wrapper = MockRedisWrapper()
    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", mock_redis_wrapper)
    
    keyring.clear()
    enc = Encryptor()
    ct = enc.encrypt("scope1", "hello")
    assert enc.decrypt("scope1", ct) is None
    
def test_active_authorized_partition_decrypts(monkeypatch):
    class MockRedisClient:
        def __init__(self):
            self.store = {}
        def get(self, key):
            return self.store.get(key)
        def set(self, key, value):
            self.store[key] = value
            
    class MockRedisWrapper:
        def __init__(self):
            self.client = MockRedisClient()

    mock_redis_wrapper = MockRedisWrapper()
    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", mock_redis_wrapper)
    
    enc = Encryptor()
    ct = enc.encrypt("scope1", "hello")
    
    keyring.add_key("scope1", enc._get_or_create_key("scope1"))
    assert enc.decrypt("scope1", ct) == "hello"
