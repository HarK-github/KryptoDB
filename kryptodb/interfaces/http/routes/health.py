"""Health and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from kryptodb.infrastructure.neo4j.client import neo4j_client

router = APIRouter(tags=["Health"])


@router.get("/health")
@router.get("/healthz")
async def health_check(request: Request):
    payload = {"status": "ok", "service": "kryptodb", "version": "0.2.0"}
    audit_status = getattr(request.app.state, "latest_plaintext_audit", None)
    if audit_status is not None:
        payload["plaintext_audit"] = audit_status
    return payload


@router.get("/ready")
async def readiness_check():
    neo4j_ok = False
    try:
        if neo4j_client.driver:
            with neo4j_client.driver.session() as s:
                s.run("RETURN 1")
            neo4j_ok = True
    except Exception:
        neo4j_ok = False

    return {
        "status": "ready" if neo4j_ok else "degraded",
        "neo4j": neo4j_ok,
    }
