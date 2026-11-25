"""Append-only SHA-256 hash-chained SQLite audit ledger."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class AuditLedger:
    """Tamper-evident append-only audit log backed by SQLite.

    Records actor, action, tenant, scope, target ID, outcome, and timestamp.
    Never logs decrypted or raw fact content.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.getenv("KRYPTODB_AUDIT_DB", "data/audit.db")
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_entries (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        target_id TEXT,
                        prev_hash TEXT NOT NULL,
                        entry_hash TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    @staticmethod
    def _compute_hash(payload: Dict[str, Any], prev_hash: str) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{prev_hash}:{canonical}".encode("utf-8")).hexdigest()

    def append(
        self,
        actor: str,
        action: str,
        tenant_id: str = "default",
        scope: str = "default",
        outcome: str = "allowed",
        target_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT entry_hash FROM audit_entries ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                prev_hash = row["entry_hash"] if row else "0" * 64
                payload = {
                    "timestamp": ts,
                    "actor": actor,
                    "action": action,
                    "tenant_id": tenant_id,
                    "scope": scope,
                    "outcome": outcome,
                    "target_id": target_id or "",
                }
                entry_hash = self._compute_hash(payload, prev_hash)
                conn.execute(
                    """
                    INSERT INTO audit_entries (
                        timestamp, actor, action, tenant_id, scope, outcome, target_id, prev_hash, entry_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, actor, action, tenant_id, scope, outcome, target_id or "", prev_hash, entry_hash),
                )
                conn.commit()
                return {**payload, "prev_hash": prev_hash, "entry_hash": entry_hash}

    def list_recent(self, tenant_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                if tenant_id:
                    rows = conn.execute(
                        """
                        SELECT seq, timestamp, actor, action, tenant_id, scope, outcome, target_id, prev_hash, entry_hash
                        FROM audit_entries
                        WHERE tenant_id = ?
                        ORDER BY seq DESC
                        LIMIT ?
                        """,
                        (tenant_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT seq, timestamp, actor, action, tenant_id, scope, outcome, target_id, prev_hash, entry_hash
                        FROM audit_entries
                        ORDER BY seq DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]

    def verify_chain(self) -> bool:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT timestamp, actor, action, tenant_id, scope, outcome, target_id, prev_hash, entry_hash
                    FROM audit_entries
                    ORDER BY seq ASC
                    """
                ).fetchall()
        expected_prev = "0" * 64
        for r in rows:
            if r["prev_hash"] != expected_prev:
                return False
            payload = {
                "timestamp": r["timestamp"],
                "actor": r["actor"],
                "action": r["action"],
                "tenant_id": r["tenant_id"],
                "scope": r["scope"],
                "outcome": r["outcome"],
                "target_id": r["target_id"] or "",
            }
            if self._compute_hash(payload, expected_prev) != r["entry_hash"]:
                return False
            expected_prev = r["entry_hash"]
        return True


audit_ledger = AuditLedger()
