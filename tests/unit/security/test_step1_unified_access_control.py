"""Step 1 Verification Gate: Fail-closed auth, SQLite key persistence, PolicyEngine, HTTP/WS/MCP/Tool unification."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from kryptodb.core.context import RequestContext
from kryptodb.core.identity.authentication import Authenticator
from kryptodb.core.identity.policy import PolicyDeniedError, PolicyEngine, load_policy_config


def test_no_dev_key_fallbacks_and_startup_guard():
    old_admin = os.environ.pop("KRYPTODB_ADMIN_API_KEY", None)
    old_file = os.environ.pop("KRYPTODB_ADMIN_API_KEY_FILE", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            auth = Authenticator(db_path=Path(td) / "keys.db")
            assert auth.verify_token("dev-admin-key") is None
            assert auth.verify_token("dev-agent-key") is None
            try:
                auth.validate_startup_config()
                raise AssertionError("Expected RuntimeError when KRYPTODB_ADMIN_API_KEY is unset")
            except RuntimeError as exc:
                assert "KRYPTODB_ADMIN_API_KEY" in str(exc)

            os.environ["KRYPTODB_ADMIN_API_KEY"] = "super-secret-admin-key"
            auth.validate_startup_config()
            claims = auth.verify_token("super-secret-admin-key")
            assert claims is not None and claims["is_admin"] is True
            assert auth.verify_token("dev-admin-key") is None
    finally:
        if old_admin is not None:
            os.environ["KRYPTODB_ADMIN_API_KEY"] = old_admin
        else:
            os.environ.pop("KRYPTODB_ADMIN_API_KEY", None)
        if old_file is not None:
            os.environ["KRYPTODB_ADMIN_API_KEY_FILE"] = old_file


def test_sqlite_key_persistence_and_revocation_across_restarts():
    with tempfile.TemporaryDirectory() as td:
        db_file = Path(td) / "api_keys.db"
        auth1 = Authenticator(db_path=db_file)
        rec = auth1.register_agent_key(
            api_key="kdb-persist-test-key",
            agent_id="research-bot",
            tenant_id="acme",
            scopes=["research", "shared"],
            role="writer",
        )
        assert auth1.verify_token("kdb-persist-test-key") is not None

        # Simulate daemon restart with a fresh Authenticator instance on the same SQLite DB
        auth2 = Authenticator(db_path=db_file)
        claims = auth2.verify_token("kdb-persist-test-key")
        assert claims is not None
        assert claims["agent_id"] == "research-bot"
        assert claims["tenant_id"] == "acme"
        assert claims["role"] == "writer"
        assert set(claims["scopes"]) == {"research", "shared"}

        # Revoke and verify persistence of revocation
        assert auth2.revoke_key(rec["key_id"]) is True
        assert auth2.verify_token("kdb-persist-test-key") is None
        auth3 = Authenticator(db_path=db_file)
        assert auth3.verify_token("kdb-persist-test-key") is None


def test_json_policy_loader_and_policy_engine():
    with tempfile.TemporaryDirectory() as td:
        policy_path = Path(td) / "policy.json"
        policy_path.write_text(
            json.dumps({
                "agents": [
                    {
                        "agent_id": "support-bot",
                        "tenant_id": "acme",
                        "role": "reader",
                        "scopes": ["support", "shared"],
                        "api_key_env": "TEST_SUPPORT_KEY",
                    }
                ]
            }),
            encoding="utf-8",
        )
        cfg = load_policy_config(policy_path)
        assert len(cfg.agents) == 1
        assert cfg.agents[0].role == "reader"

        os.environ["TEST_SUPPORT_KEY"] = "kdb-support-reader-key"
        try:
            auth = Authenticator(db_path=Path(td) / "keys.db")
            assert auth.load_policy_file(policy_path) == 1
            claims = auth.verify_token("kdb-support-reader-key")
            assert claims is not None
            ctx = RequestContext.create(
                tenant_id=claims["tenant_id"],
                agent_id=claims["agent_id"],
                active_scopes=claims["scopes"],
                role=claims["role"],
                permissions=claims["permissions"],
                is_system=False,
            )
            pe = PolicyEngine()
            # Allowed read in authorized scope
            assert pe.authorize(ctx, "memory:read", target_scopes=["shared"]) == ["shared"]
            # Denied write for reader role
            try:
                pe.authorize(ctx, "memory:write", target_scopes=["shared"])
                raise AssertionError("Reader must be denied memory:write")
            except PolicyDeniedError:
                pass
            # Denied out-of-scope read
            try:
                pe.authorize(ctx, "memory:read", target_scopes=["finance"])
                raise AssertionError("Out-of-scope read must be denied")
            except PolicyDeniedError:
                pass
            # Denied cross-tenant access
            try:
                pe.authorize(ctx, "memory:read", target_tenant="other-tenant", target_scopes=["shared"])
                raise AssertionError("Cross-tenant access must be denied")
            except PolicyDeniedError:
                pass
            # Denied another agent's thread
            try:
                pe.authorize(ctx, "chat:read", target_agent_id="research-bot")
                raise AssertionError("Cross-agent thread access must be denied")
            except PolicyDeniedError:
                pass
        finally:
            os.environ.pop("TEST_SUPPORT_KEY", None)


def test_mcp_fail_closed_without_valid_api_key():
    from kryptodb.core.identity.authentication import authenticator
    from kryptodb.interfaces.mcp.server import _get_mcp_context

    old_key = os.environ.pop("KRYPTODB_API_KEY", None)
    try:
        try:
            _get_mcp_context()
            raise AssertionError("MCP must fail closed without KRYPTODB_API_KEY")
        except PolicyDeniedError:
            pass

        authenticator.register_agent_key(
            api_key="kdb-mcp-valid-key",
            agent_id="mcp-agent",
            tenant_id="acme",
            scopes=["research"],
            role="writer",
        )
        os.environ["KRYPTODB_API_KEY"] = "kdb-mcp-valid-key"
        ctx = _get_mcp_context()
        assert ctx.agent_id == "mcp-agent"
        assert ctx.tenant_id == "acme"
        assert set(ctx.active_scopes) == {"research"}
    finally:
        if old_key is not None:
            os.environ["KRYPTODB_API_KEY"] = old_key
        else:
            os.environ.pop("KRYPTODB_API_KEY", None)


if __name__ == "__main__":
    test_no_dev_key_fallbacks_and_startup_guard()
    test_sqlite_key_persistence_and_revocation_across_restarts()
    test_json_policy_loader_and_policy_engine()
    test_mcp_fail_closed_without_valid_api_key()
    print("ALL STEP 1 UNIFIED ACCESS-CONTROL TESTS PASSED (4/4)")
