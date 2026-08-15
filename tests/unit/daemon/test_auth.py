from kryptodb.daemon.auth import ApiKeyRegistry, parse_bearer


def test_bearer_parser_is_strict():
    assert parse_bearer("Bearer syn_test") == "syn_test"
    assert parse_bearer("Basic syn_test") is None
    assert parse_bearer(None) is None


def test_api_key_authentication_and_scope_limit(monkeypatch):
    monkeypatch.delenv("KRYPTODB_ADMIN_API_KEY", raising=False)
    registry = ApiKeyRegistry()
    raw_key, record = registry.create_key("Agent-1", ["research"])

    assert raw_key.startswith("syn_live_")
    assert "raw_key" not in record
    context = registry.authenticate(raw_key)
    assert context is not None
    assert context.agent_id == "Agent-1"
    assert context.scopes == {"research"}
    assert registry.authenticate("syn_live_invalid") is None


def test_revocation_invalidates_key(monkeypatch):
    monkeypatch.delenv("KRYPTODB_ADMIN_API_KEY", raising=False)
    registry = ApiKeyRegistry()
    raw_key, record = registry.create_key("Agent-1", ["research"])
    assert registry.revoke(record["key_id"])
    assert registry.authenticate(raw_key) is None


def test_key_listing_never_returns_secret(monkeypatch):
    monkeypatch.delenv("KRYPTODB_ADMIN_API_KEY", raising=False)
    registry = ApiKeyRegistry()
    raw_key, record = registry.create_key("Agent-1", ["research"])
    listed = registry.list_keys()
    assert listed[0]["key_id"] == record["key_id"]
    assert raw_key not in str(listed)
