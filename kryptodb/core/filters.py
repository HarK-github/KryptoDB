"""FilterBuilder: The single choke point for all Cypher query filters in KryptoDB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from kryptodb.core.context import RequestContext


class FilterBuilder:
    """The authoritative choke point for all Cypher filters.
    
    No Cypher query should access `:FACT` edges without passing through this filter.
    It guarantees tenant isolation, active scope containment, fact lifecycle status,
    and temporal validity bounds.
    """

    @staticmethod
    def build(
        context: RequestContext,
        alias: str = "r",
        system_mode: bool = False,
    ) -> Tuple[str, Dict[str, Any]]:
        """Construct parameterized Cypher WHERE clause and parameters from RequestContext."""
        now_val = context.now.isoformat()
        
        # If not in system mode and active_scopes is empty, match NOTHING
        if not system_mode and not context.is_system and not context.active_scopes:
            return (
                "1 = 0",
                {
                    "tenant_id": context.tenant_id,
                    "now": now_val,
                    "status": "active",
                }
            )

        clauses = [
            f"{alias}.tenant_id = $tenant_id",
            f"{alias}.status = $status",
            f"{alias}.valid_from <= $now",
            f"({alias}.valid_until IS NULL OR {alias}.valid_until > $now)",
        ]

        params: Dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "status": "active",
            "now": now_val,
        }

        # Apply active_scopes filter unless in system mode
        if not system_mode and not context.is_system:
            clauses.append(f"{alias}.scope IN $active_scopes")
            params["active_scopes"] = sorted(list(context.active_scopes))

        return " AND ".join(clauses), params

    @classmethod
    def build_edge_filter(
        cls,
        tenant_id: str = "default",
        active_scopes: Optional[Union[Sequence[str], set[str]]] = None,
        status: str = "active",
        now: Optional[datetime] = None,
        edge_alias: str = "edge",
    ) -> Tuple[str, Dict[str, Any]]:
        """Backward-compatible helper for legacy callers."""
        dt_now = now or datetime.now(timezone.utc)
        clauses = [
            f"{edge_alias}.tenant_id = $tenant_id",
            f"{edge_alias}.status = $status",
            f"({edge_alias}.valid_until IS NULL OR {edge_alias}.valid_until > $now_iso)",
            f"({edge_alias}.valid_from IS NULL OR {edge_alias}.valid_from <= $now_iso)",
        ]
        params: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "status": status,
            "now_iso": dt_now.isoformat(),
            "now": dt_now.isoformat(),
        }
        if active_scopes:
            clauses.append(f"{edge_alias}.scope IN $scopes")
            params["scopes"] = list(active_scopes)
            params["active_scopes"] = list(active_scopes)
        return " AND ".join(clauses), params
