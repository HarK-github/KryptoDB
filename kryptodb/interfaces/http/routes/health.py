"""Health and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter
from kryptodb.storage.neo4j_client import neo4j_client

router = APIRouter(tags=["Health"])


@router.get("/health")
@router.get("/healthz")
async def health_check():
    return {"status": "ok", "service": "kryptodb", "version": "0.2.0"}


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
