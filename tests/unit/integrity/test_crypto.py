import pytest
from kryptodb.integrity.partition_crypto import Encryptor
from kryptodb.daemon.keyring import keyring

def test_partition_key_created_on_first_write(monkeypatch):
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
    assert "key:scope1" in mock_redis_wrapper.client.store

def test_repeated_partition_write_reuses_key(monkeypatch):
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
    enc.encrypt("scope1", "hello")
    enc.encrypt("scope1", "world")
    
    assert len(mock_redis_wrapper.client.store) == 1

def test_different_partitions_use_different_keys(monkeypatch):
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
    enc.encrypt("scope1", "hello")
    enc.encrypt("scope2", "hello")
    
    assert mock_redis_wrapper.client.store["key:scope1"] != mock_redis_wrapper.client.store["key:scope2"]
    
def test_content_encrypts_at_write_time(monkeypatch):
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
    assert "hello" not in ct
    assert ct.count(":") == 1

def test_wrong_partition_key_cannot_decrypt(monkeypatch):
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
    
    keyring.clear()
    enc.encrypt("scope2", "other") 
    wrong_base_key = enc._get_or_create_key("scope2")
    keyring.add_key("scope1", wrong_base_key)
    
    assert enc.decrypt("scope1", ct) is None
    
def test_inactive_partition_has_no_session_key():
    keyring.clear()
    enc = Encryptor()
    assert enc.decrypt("inactive_scope", "fake:ct") is None

def test_daemon_restart_does_not_restore_session_keys():
    keyring.clear()
    assert len(keyring._keys) == 0


def test_each_knowledge_item_gets_a_unique_dek(monkeypatch):
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

    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", MockRedisWrapper())
    enc = Encryptor()
    first = enc.encrypt_knowledge("scope1", "fact-1", {"subject": "A", "predicate": "KNOWS", "object": "B"})
    second = enc.encrypt_knowledge("scope1", "fact-2", {"subject": "A", "predicate": "KNOWS", "object": "B"})
    assert first["knowledge_ct"] != second["knowledge_ct"]
    assert first["wrapped_dek"] != second["wrapped_dek"]


def test_knowledge_envelope_requires_active_partition_key(monkeypatch):
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

    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", MockRedisWrapper())
    enc = Encryptor()
    record = enc.encrypt_knowledge("scope1", "fact-1", {"secret": "value"})
    keyring.clear()
    assert enc.decrypt_knowledge("scope1", "fact-1", record) is None
    keyring.add_key("scope1", enc._get_or_create_key("scope1"))
    assert enc.decrypt_knowledge("scope1", "fact-1", record)["secret"] == "value"
