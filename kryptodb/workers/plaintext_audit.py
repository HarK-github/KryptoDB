"""Phase 3 Zero-Key-Access Allowlist Plaintext Audit Worker (v3 Section 9).

Scans Neo4j against strict property allowlists without holding or importing any
cryptographic keys. Flags any disallowed property or active record missing
ciphertext, increments kryptodb_plaintext_violation_total, and alerts.
Never seals or mutates data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Set

from kryptodb.infrastructure.neo4j.encrypted_queries import (
    AUDIT_ENTITY_PROPERTIES_QUERY,
    AUDIT_EPISODE_PROPERTIES_QUERY,
    AUDIT_EVENT_PROPERTIES_QUERY,
    AUDIT_FACT_PROPERTIES_QUERY,
)

logger = logging.getLogger("kryptodb.workers.plaintext_audit")

ALLOWED_ENTITY_PROPS: FrozenSet[str] = frozenset({
    "id",
    "scope",
    "name_bidx",
    "name_ct",
    "wrapped_dek",
    "kid",
    "created_at",
    "updated_at",
})

ALLOWED_FACT_PROPS: FrozenSet[str] = frozenset({
    "id",
    "scope",
    "status",
    "version",
    "previous_version_id",
    "superseded_by",
    "valid_from",
    "valid_until",
    "recorded_from",
    "recorded_to",
    "confidence",
    "trust_weight",
    "subject_bidx",
    "predicate_bidx",
    "object_bidx",
    "search_tokens",
    "knowledge_ct",
    "wrapped_dek",
    "kid",
    "created_at",
})

ALLOWED_EVENT_PROPS: FrozenSet[str] = frozenset({
    "id",
    "session_id",
    "scope",
    "timestamp",
    "event_type",
    "content_ct",
    "wrapped_dek",
    "kid",
})

ALLOWED_EPISODE_PROPS: FrozenSet[str] = frozenset({
    "id",
    "session_id",
    "scope",
    "timestamp",
    "summary_ct",
    "wrapped_dek",
    "kid",
})


@dataclass
class PlaintextViolation:
    kind: str
    record_id: str
    reason: str
    disallowed_props: List[str] = field(default_factory=list)


@dataclass
class PlaintextAuditReport:
    scanned_entities: int = 0
    scanned_facts: int = 0
    scanned_events: int = 0
    scanned_episodes: int = 0
    violations: List[PlaintextViolation] = field(default_factory=list)

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    @property
    def clean(self) -> bool:
        return len(self.violations) == 0


class PlaintextAuditScanner:
    """Zero-key-access scanner enforcing v3 Section 9 strict allowlist invariants."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.total_violations_metric: int = 0

    def _run(self, query: str) -> List[Dict[str, Any]]:
        if not self.client or not getattr(self.client, "driver", None):
            raise RuntimeError("Neo4j driver is not connected for plaintext audit.")
        if hasattr(self.client, "execute_query_with_retry"):
            rows, _, _ = self.client.execute_query_with_retry(query)
        else:
            rows, _, _ = self.client.driver.execute_query(query)
        return [dict(r) for r in rows]

    @staticmethod
    def _check_allowlist(
        kind: str,
        record_id: str,
        prop_keys: Set[str],
        allowed: FrozenSet[str],
        require_ct: bool,
        has_ct: bool,
        has_dek: bool,
    ) -> List[PlaintextViolation]:
        issues: List[PlaintextViolation] = []
        extra = sorted(prop_keys - allowed)
        if extra:
            issues.append(
                PlaintextViolation(
                    kind=kind,
                    record_id=record_id,
                    reason="disallowed_properties",
                    disallowed_props=extra,
                )
            )
        if require_ct and (not has_ct or not has_dek):
            issues.append(
                PlaintextViolation(
                    kind=kind,
                    record_id=record_id,
                    reason="missing_ciphertext_or_wrapped_dek",
                )
            )
        return issues

    def scan(self) -> PlaintextAuditReport:
        report = PlaintextAuditReport()

        for row in self._run(AUDIT_ENTITY_PROPERTIES_QUERY):
            report.scanned_entities += 1
            report.violations.extend(
                self._check_allowlist(
                    kind="Entity",
                    record_id=str(row.get("id", "")),
                    prop_keys=set(row.get("prop_keys") or []),
                    allowed=ALLOWED_ENTITY_PROPS,
                    require_ct=True,
                    has_ct=bool(row.get("has_ct")),
                    has_dek=bool(row.get("has_dek")),
                )
            )

        for row in self._run(AUDIT_FACT_PROPERTIES_QUERY):
            report.scanned_facts += 1
            status = str(row.get("status") or "active")
            # Revoked (forgotten) edges legitimately have knowledge_ct=NULL; active/superseded must be sealed.
            require_ct = status != "revoked"
            report.violations.extend(
                self._check_allowlist(
                    kind="FACT",
                    record_id=str(row.get("id", "")),
                    prop_keys=set(row.get("prop_keys") or []),
                    allowed=ALLOWED_FACT_PROPS,
                    require_ct=require_ct,
                    has_ct=bool(row.get("has_ct")),
                    has_dek=bool(row.get("has_dek")),
                )
            )

        for row in self._run(AUDIT_EVENT_PROPERTIES_QUERY):
            report.scanned_events += 1
            report.violations.extend(
                self._check_allowlist(
                    kind="Event",
                    record_id=str(row.get("id", "")),
                    prop_keys=set(row.get("prop_keys") or []),
                    allowed=ALLOWED_EVENT_PROPS,
                    require_ct=True,
                    has_ct=bool(row.get("has_ct")),
                    has_dek=bool(row.get("has_dek")),
                )
            )

        for row in self._run(AUDIT_EPISODE_PROPERTIES_QUERY):
            report.scanned_episodes += 1
            report.violations.extend(
                self._check_allowlist(
                    kind="Episode",
                    record_id=str(row.get("id", "")),
                    prop_keys=set(row.get("prop_keys") or []),
                    allowed=ALLOWED_EPISODE_PROPS,
                    require_ct=True,
                    has_ct=bool(row.get("has_ct")),
                    has_dek=bool(row.get("has_dek")),
                )
            )

        if report.violations:
            self.total_violations_metric += report.violation_count
            logger.critical(
                "ALERT [kryptodb_plaintext_violation_total += %d]: %s",
                report.violation_count,
                [(v.kind, v.record_id, v.reason, v.disallowed_props) for v in report.violations[:5]],
            )

        return report
