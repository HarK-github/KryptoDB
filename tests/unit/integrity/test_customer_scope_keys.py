import base64
import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kryptodb.daemon import keyring
from kryptodb.integrity.partition_crypto import Encryptor


def configure_customer_key(monkeypatch, scope="diagnosis"):
    key = AESGCM.generate_key(bit_length=256)
    monkeypatch.setenv("KRYPTODB_ENV", "development")
    monkeypatch.setenv(
        "KRYPTODB_SCOPE_KEYS_JSON",
        json.dumps({scope: {"key_id": f"{scope}:v1", "key": base64.b64encode(key).decode()}}),
    )
    return key


def test_customer_scope_key_encrypts_without_redis_storage(monkeypatch):
    configure_customer_key(monkeypatch)

    class Redis:
        def __init__(self):
            self.store = {}

        def get(self, key):
            return self.store.get(key)

        def set(self, key, value):
            self.store[key] = value

    class Wrapper:
        client = Redis()

    monkeypatch.setattr("kryptodb.integrity.partition_crypto.redis_client", Wrapper())
    enc = Encryptor()
    record = enc.encrypt_knowledge("diagnosis", "fact-1", {"subject": "A", "predicate": "HAS", "object": "B"})
    assert "key:diagnosis" not in Wrapper.client.store
    keyring.clear()
    keyring.add_key("diagnosis", enc._get_or_create_key("diagnosis"))
    assert enc.decrypt_knowledge("diagnosis", "fact-1", record)["subject"] == "A"
    assert record["knowledge_kek_id"] == "diagnosis:v1"


def test_customer_scope_keys_are_isolated(monkeypatch):
    diagnosis = configure_customer_key(monkeypatch, "diagnosis")
    patient = AESGCM.generate_key(bit_length=256)
    monkeypatch.setenv(
        "KRYPTODB_SCOPE_KEYS_JSON",
        json.dumps({
            "diagnosis": {"key_id": "diagnosis:v1", "key": base64.b64encode(diagnosis).decode()},
            "patients": {"key_id": "patients:v1", "key": base64.b64encode(patient).decode()},
        }),
    )
    enc = Encryptor()
    record = enc.encrypt_knowledge("diagnosis", "fact-1", {"secret": "diagnosis-only"})
    keyring.clear()
    keyring.add_key("patients", enc._get_or_create_key("patients"))
    assert enc.decrypt_knowledge("diagnosis", "fact-1", record) is None


def test_production_requires_configured_customer_scope_key(monkeypatch):
    monkeypatch.setenv("KRYPTODB_ENV", "production")
    monkeypatch.setenv("KRYPTODB_MASTER_KEY", "00" * 32)
    monkeypatch.delenv("KRYPTODB_SCOPE_KEYS_JSON", raising=False)
    monkeypatch.delenv("KRYPTODB_SCOPE_KEYS_FILE", raising=False)
    enc = Encryptor()
    with pytest.raises(RuntimeError, match="Customer scope key"):
        enc._get_or_create_key("diagnosis")
