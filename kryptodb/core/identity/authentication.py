"""Framework-free Authenticator with SHA-256 hashed SQLite persistence and fail-closed admin key."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from kryptodb.core.identity.policy import ROLE_PERMISSIONS, load_policy_config


class Authenticator:
    """Validates API keys with SHA-256 hashing, constant-time comparison, and SQLite persistence.

    Zero hardcoded dev keys. Requires KRYPTODB_ADMIN_API_KEY (or _FILE) for admin operations.
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self._lock = threading.RLock()
        self._agent_keys: Dict[str, Dict[str, Any]] = {}
        self._db_path = self._resolve_db_path(db_path)
        self._conn = self._init_db(self._db_path)
        self._load_keys_from_db()

    @staticmethod
    def _resolve_db_path(explicit: Optional[str | Path]) -> str:
        if explicit is not None:
            return str(explicit)
        env_path = os.getenv("KRYPTODB_KEY_DB_PATH")
        if env_path:
            return env_path
        return "data/api_keys.db"

    @staticmethod
    def _init_db(db_path: str) -> sqlite3.Connection:
        if db_path == ":memory:":
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            try:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(db_path, check_same_thread=False)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to initialize SQLite API key store at '{db_path}': {exc}. "
                    "Refusing to fall back to ephemeral :memory: SQLite."
                ) from exc
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash TEXT PRIMARY KEY,
                key_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'writer',
                scopes_json TEXT NOT NULL,
                permissions_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_at TEXT
            )
            """
        )
        conn.commit()
        return conn

    def _load_keys_from_db(self) -> None:
        with self._lock:
            self._agent_keys.clear()
            cur = self._conn.execute(
                "SELECT key_hash, key_id, agent_id, tenant_id, role, scopes_json, permissions_json, status, created_at, expires_at, revoked_at FROM api_keys"
            )
            for row in cur.fetchall():
                key_hash, key_id, agent_id, tenant_id, role, scopes_json, perms_json, status_val, created_at, expires_at, revoked_at = row
                self._agent_keys[key_hash] = {
                    "key_id": key_id,
                    "agent_id": agent_id,
                    "tenant_id": tenant_id,
                    "role": role or "writer",
                    "scopes": json.loads(scopes_json or '["default"]'),
                    "permissions": json.loads(perms_json or "[]"),
                    "status": status_val or "active",
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "revoked_at": revoked_at,
                }

    def reconfigure_db(self, db_path: str | Path) -> None:
        """Switches the backing SQLite database (useful for tests and runtime reconfiguration)."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
            self._db_path = str(db_path)
            self._conn = self._init_db(self._db_path)
            self._load_keys_from_db()

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _effective_admin_key() -> Optional[str]:
        configured = os.getenv("KRYPTODB_ADMIN_API_KEY")
        if configured and configured.strip():
            return configured.strip()
        key_file = os.getenv("KRYPTODB_ADMIN_API_KEY_FILE")
        if key_file and Path(key_file).is_file():
            val = Path(key_file).read_text(encoding="utf-8").strip()
            if val:
                return val
        return None

    def validate_startup_config(self) -> None:
        """Refuses to start if KRYPTODB_ADMIN_API_KEY is missing."""
        if not self._effective_admin_key():
            raise RuntimeError(
                "KRYPTODB_ADMIN_API_KEY (or KRYPTODB_ADMIN_API_KEY_FILE) is required at startup. "
                "Default development keys have been removed."
            )

    def load_policy_file(self, path: Optional[str | Path] = None) -> int:
        """Bootstraps agent policies from a JSON policy file (KRYPTODB_POLICY_FILE)."""
        resolved = path or os.getenv("KRYPTODB_POLICY_FILE")
        if not resolved:
            return 0
        cfg = load_policy_config(resolved)
        loaded = 0
        for entry in cfg.agents:
            raw_key = os.getenv(entry.api_key_env) if entry.api_key_env else None
            if raw_key:
                self.register_agent_key(
                    api_key=raw_key,
                    agent_id=entry.agent_id,
                    tenant_id=entry.tenant_id,
                    scopes=entry.scopes,
                    role=entry.role,
                    permissions=entry.permissions,
                )
                loaded += 1
        return loaded

    def register_agent_key(
        self,
        api_key: str,
        agent_id: str,
        tenant_id: str = "default",
        scopes: Optional[List[str]] = None,
        key_id: Optional[str] = None,
        role: str = "writer",
        permissions: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        key_hash = self._hash_key(api_key)
        resolved_key_id = key_id or f"key_{key_hash[:12]}"
        clean_role = (role or "writer").strip().lower()
        clean_scopes = sorted(set(scopes or ["default"]))
        clean_perms = sorted(set(permissions)) if permissions is not None else sorted(ROLE_PERMISSIONS.get(clean_role, frozenset()))
        now_iso = datetime.now(timezone.utc).isoformat()
        record = {
            "key_id": resolved_key_id,
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "role": clean_role,
            "scopes": clean_scopes,
            "permissions": clean_perms,
            "status": "active",
            "created_at": now_iso,
            "expires_at": expires_at,
            "revoked_at": None,
        }
        with self._lock:
            self._agent_keys[key_hash] = record
            self._conn.execute(
                """
                INSERT OR REPLACE INTO api_keys
                (key_hash, key_id, agent_id, tenant_id, role, scopes_json, permissions_json, status, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_hash,
                    resolved_key_id,
                    agent_id,
                    tenant_id,
                    clean_role,
                    json.dumps(clean_scopes),
                    json.dumps(clean_perms),
                    "active",
                    now_iso,
                    expires_at,
                    None,
                ),
            )
            self._conn.commit()
        return dict(record)

    def list_agents(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            agents_map: Dict[str, Dict[str, Any]] = {}
            for meta in self._agent_keys.values():
                if meta.get("status") != "active":
                    continue
                if tenant_id and meta.get("tenant_id") != tenant_id:
                    continue
                aid = meta["agent_id"]
                if aid in agents_map:
                    existing_scopes = set(agents_map[aid]["allowed_scopes"])
                    existing_scopes.update(meta.get("scopes", []))
                    agents_map[aid]["allowed_scopes"] = sorted(existing_scopes)
                else:
                    agents_map[aid] = {
                        "id": aid,
                        "allowed_scopes": sorted(set(meta.get("scopes", ["default"]))),
                        "tenant_id": meta.get("tenant_id", "default"),
                        "role": meta.get("role", "writer"),
                        "status": "active",
                    }
            return list(agents_map.values())

    def update_agent_scopes(
        self, agent_id: str, scopes: List[str], tenant_id: str = "default"
    ) -> None:
        clean_scopes = sorted(set(scopes))
        with self._lock:
            updated = False
            for key_hash, meta in self._agent_keys.items():
                if meta["agent_id"] == agent_id and meta.get("tenant_id") == tenant_id:
                    meta["scopes"] = clean_scopes
                    self._conn.execute(
                        "UPDATE api_keys SET scopes_json = ? WHERE key_hash = ?",
                        (json.dumps(clean_scopes), key_hash),
                    )
                    updated = True
            if updated:
                self._conn.commit()
            else:
                self.register_agent_key(
                    f"kdb-{secrets.token_urlsafe(32)}",
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    scopes=clean_scopes,
                )

    def list_keys(self) -> List[Dict[str, Any]]:
        """Return non-secret key metadata only (never returns raw API keys)."""
        with self._lock:
            return [
                {
                    "key_id": v["key_id"],
                    "agent_id": v["agent_id"],
                    "tenant_id": v["tenant_id"],
                    "role": v.get("role", "writer"),
                    "scopes": list(v["scopes"]),
                    "permissions": list(v.get("permissions", [])),
                    "status": v.get("status", "active"),
                    "expires_at": v.get("expires_at"),
                    "revoked_at": v.get("revoked_at"),
                }
                for v in self._agent_keys.values()
            ]

    def revoke_key(self, key_id: str) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            changed = False
            for key_hash, meta in list(self._agent_keys.items()):
                if meta.get("key_id") == key_id or secrets.compare_digest(key_hash, self._hash_key(key_id)):
                    meta["status"] = "revoked"
                    meta["revoked_at"] = now_iso
                    self._conn.execute(
                        "UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE key_hash = ?",
                        (now_iso, key_hash),
                    )
                    changed = True
            if changed:
                self._conn.commit()
            return changed

    @staticmethod
    def _is_expired(expires_at: Optional[str]) -> bool:
        if not expires_at:
            return False
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= exp
        except Exception:
            return True

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None

        admin_key = self._effective_admin_key()
        if admin_key and secrets.compare_digest(token, admin_key):
            return {
                "agent_id": "admin",
                "tenant_id": "system",
                "is_admin": True,
                "role": "admin",
                "scopes": ["*"],
                "permissions": ["*"],
            }

        token_hash = self._hash_key(token)
        with self._lock:
            meta = self._agent_keys.get(token_hash)
            if meta and meta.get("status") == "active" and not self._is_expired(meta.get("expires_at")):
                role = meta.get("role", "writer")
                perms = list(meta.get("permissions") or sorted(ROLE_PERMISSIONS.get(role, frozenset())))
                return {
                    "key_id": meta["key_id"],
                    "agent_id": meta["agent_id"],
                    "tenant_id": meta["tenant_id"],
                    "role": role,
                    "scopes": list(meta["scopes"]),
                    "permissions": perms,
                    "is_admin": role == "admin",
                }

        return None


authenticator = Authenticator()
