"""Unit tests verifying the FilterBuilder security choke point."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta
import pytest

from kryptodb.core.context import RequestContext, create_system_context
from kryptodb.core.filters import FilterBuilder


def test_request_context_validation():
    """Verify RequestContext enforces non-empty strings and timezone awareness."""
    with pytest.raises(ValueError, match="tenant_id"):
        RequestContext.create(tenant_id="", agent_id="agent-1")

    with pytest.raises(ValueError, match="agent_id"):
        RequestContext.create(tenant_id="tenant-1", agent_id="   ")

    # Timezone awareness
    naive_dt = datetime(2026, 1, 1, 12, 0, 0)
    ctx = RequestContext.create(tenant_id="t1", agent_id="a1", now=naive_dt)
    assert ctx.now.tzinfo is not None
    assert ctx.now.tzinfo == timezone.utc


def test_filter_builder_normal_scopes():
    """Verify standard scope, tenant, status, and temporal filtering."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    ctx = RequestContext.create(
        tenant_id="acme",
        agent_id="agent-smith",
        active_scopes=["research", "engineering"],
        now=now,
    )
    where_clause, params = FilterBuilder.build(ctx, alias="r")

    assert "r.tenant_id = $tenant_id" in where_clause
    assert "r.scope IN $active_scopes" in where_clause
    assert "r.status = $status" in where_clause
    assert "r.valid_from <= $now" in where_clause
    assert "r.valid_until IS NULL OR r.valid_until > $now" in where_clause

    assert params["tenant_id"] == "acme"
    assert params["status"] == "active"
    assert params["active_scopes"] == ["engineering", "research"]
    assert params["now"] == now.isoformat()


def test_filter_builder_empty_scopes_blocks_everything():
    """If active_scopes is empty and not in system mode, the clause must evaluate to 1 = 0."""
    ctx = RequestContext.create(
        tenant_id="acme",
        agent_id="agent-smith",
        active_scopes=[],
    )
    where_clause, params = FilterBuilder.build(ctx, alias="r")
    assert where_clause == "1 = 0"
    assert params["tenant_id"] == "acme"


def test_filter_builder_system_mode_omits_scopes():
    """System context retains tenant, status, and temporal validity but omits scope restrictions."""
    ctx = create_system_context(tenant_id="corp-sys")
    where_clause, params = FilterBuilder.build(ctx, alias="edge", system_mode=True)

    assert "edge.tenant_id = $tenant_id" in where_clause
    assert "edge.status = $status" in where_clause
    assert "edge.scope IN $active_scopes" not in where_clause
    assert "active_scopes" not in params
    assert params["tenant_id"] == "corp-sys"
    assert params["status"] == "active"


def test_filter_builder_custom_alias():
    """Verify custom relationship alias is properly injected into Cypher clause."""
    ctx = RequestContext.create(
        tenant_id="t1",
        agent_id="a1",
        active_scopes=["default"],
    )
    where_clause, _ = FilterBuilder.build(ctx, alias="fact_rel")
    assert "fact_rel.tenant_id = $tenant_id" in where_clause
    assert "fact_rel.scope IN $active_scopes" in where_clause


def test_ast_scan_choke_point_enforcement():
    """Scan all files in core/ and infrastructure/ ensuring any query touching :FACT

    references the filter clause or FilterBuilder.
    """
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    kryptodb_dir = os.path.join(root_dir, "kryptodb")
    target_dirs = [
        os.path.join(kryptodb_dir, "core"),
        os.path.join(kryptodb_dir, "infrastructure"),
    ]

    fact_pattern = re.compile(r"-\s*\[\s*(\w+)?\s*:\s*FACT\s*\]\s*->|:\s*FACT\b", re.IGNORECASE)
    filter_guard = re.compile(r"filter|FilterBuilder|where_clause|filter_clause|\$tenant_id", re.IGNORECASE)

    violations = []
    for target in target_dirs:
        for dirpath, _, filenames in os.walk(target):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, fname)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                # Search lines for :FACT match queries
                lines = content.split("\n")
                for idx, line in enumerate(lines):
                    # Check for cypher MATCH statements with FACT
                    if "MATCH" in line and fact_pattern.search(line):
                        # Verify nearby lines contain filter protection
                        context_window = "\n".join(lines[max(0, idx - 5):min(len(lines), idx + 25)])
                        if not filter_guard.search(context_window):
                            violations.append(f"{filepath}:{idx + 1}: {line.strip()}")

    assert not violations, f"Found ungoverned Cypher queries touching :FACT without filter:\n" + "\n".join(violations)
