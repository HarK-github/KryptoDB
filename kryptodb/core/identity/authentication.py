from __future__ import annotations

import os
import secrets
from typing import Optional, Dict, Any
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security_scheme = HTTPBearer(auto_error=False)

class Authenticator:
    """Validates API keys with constant-time comparison to prevent timing attacks."""
    def __init__(self):
        self.admin_api_key = os.getenv("KRYPTODB_ADMIN_API_KEY", "dev-admin-key")
        self._agent_keys: Dict[str, Dict[str, Any]] = {
            "dev-agent-key": {
                "agent_id": "Agent-1",
                "tenant_id": "default",
                "scopes": ["default", "research", "engineering"]
            }
        }

    def register_agent_key(self, api_key: str, agent_id: str, tenant_id: str = "default", scopes: Optional[list] = None) -> None:
        self._agent_keys[api_key] = {
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "scopes": scopes or ["default"]
        }

    def list_agents(self) -> list[Dict[str, Any]]:
        agents_map: Dict[str, Dict[str, Any]] = {
            "Agent-1": {"id": "Agent-1", "allowed_scopes": ["default", "research", "engineering"], "tenant_id": "default", "status": "active"},
            "campus-bot": {"id": "campus-bot", "allowed_scopes": ["PROJECT:campus", "research"], "tenant_id": "default", "status": "active"},
            "claude-desktop": {"id": "claude-desktop", "allowed_scopes": ["PROJECT:kryptodb", "default"], "tenant_id": "default", "status": "active"},
            "cursor-agent": {"id": "cursor-agent", "allowed_scopes": ["PROJECT:kryptodb", "research"], "tenant_id": "default", "status": "active"}
        }
        for meta in self._agent_keys.values():
            aid = meta["agent_id"]
            if aid in agents_map:
                existing_scopes = set(agents_map[aid]["allowed_scopes"])
                existing_scopes.update(meta.get("scopes", []))
                agents_map[aid]["allowed_scopes"] = list(existing_scopes)
            else:
                agents_map[aid] = {
                    "id": aid,
                    "allowed_scopes": meta.get("scopes", ["default"]),
                    "tenant_id": meta.get("tenant_id", "default"),
                    "status": "active"
                }
        return list(agents_map.values())

    def update_agent_scopes(self, agent_id: str, scopes: list[str]) -> None:
        updated = False
        for meta in self._agent_keys.values():
            if meta["agent_id"] == agent_id:
                meta["scopes"] = scopes
                updated = True
        if not updated:
            self.register_agent_key(f"kdb-{secrets.token_urlsafe(16)}", agent_id=agent_id, scopes=scopes)

    def list_keys(self) -> list[Dict[str, Any]]:
        return [
            {
                "key_id": f"{k[:10]}...",
                "full_key": k,
                "agent_id": v["agent_id"],
                "tenant_id": v["tenant_id"],
                "scopes": v["scopes"],
                "status": "active"
            }
            for k, v in self._agent_keys.items()
        ]

    def revoke_key(self, key_id: str) -> bool:
        to_del = [
            k for k in self._agent_keys 
            if k == key_id or k.startswith(key_id.rstrip(".")) or f"{k[:10]}..." == key_id
        ]
        for k in to_del:
            del self._agent_keys[k]
        return len(to_del) > 0

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        # Check Admin Key dynamically (support configured key and dev admin key)
        admin_key = os.getenv("KRYPTODB_ADMIN_API_KEY", self.admin_api_key or "dev-admin-key")
        is_admin = False
        if admin_key and secrets.compare_digest(token, admin_key):
            is_admin = True
        elif secrets.compare_digest(token, "dev-admin-key"):
            is_admin = True
        elif self.admin_api_key and secrets.compare_digest(token, self.admin_api_key):
            is_admin = True

        if is_admin:
            return {
                "agent_id": "admin",
                "tenant_id": "system",
                "is_admin": True,
                "scopes": ["*"]
            }

        # Check Agent Keys
        for key, meta in self._agent_keys.items():
            if secrets.compare_digest(token, key):
                return {**meta, "is_admin": False}

        return None

authenticator = Authenticator()

def require_auth(credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme)) -> Dict[str, Any]:
    if not credentials or not credentials.credentials:
        # Development fallback if in development mode
        if os.getenv("KRYPTODB_ENV", "development") == "development":
            return {"agent_id": "Agent-1", "tenant_id": "default", "is_admin": False, "scopes": ["default"]}
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authentication token")

    claims = authenticator.verify_token(credentials.credentials)
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return claims
