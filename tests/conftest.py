import os
import pytest

# Ensure standard test admin key and deterministic test LLM are configured
os.environ.setdefault("KRYPTODB_ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("LLM_MODEL", "deterministic-engine")


@pytest.fixture(autouse=True)
def ensure_default_test_api_keys():
    """Ensures test admin key is restored after tests that temporarily modify env."""
    yield
    if "KRYPTODB_ADMIN_API_KEY" not in os.environ:
        os.environ["KRYPTODB_ADMIN_API_KEY"] = "test-admin-key"
