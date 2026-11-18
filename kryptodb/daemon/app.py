from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kryptodb.interfaces.http.routes.memory import router as memory_router
from kryptodb.interfaces.http.routes.admin import router as admin_router
from kryptodb.interfaces.http.routes.health import router as health_router
from kryptodb.interfaces.http.routes.graph import router as graph_router
from kryptodb.interfaces.http.routes.chat import router as chat_router
from kryptodb.interfaces.http.routes.v1_routes import v1_router
from kryptodb.infrastructure.neo4j.schema import ensure_schema
from kryptodb.storage.neo4j_client import neo4j_client

logger = logging.getLogger("kryptodb.app")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure storage connection and schema
    try:
        from kryptodb.storage import bootstrap_storage
        bootstrap_storage()
        if neo4j_client.driver:
            report = ensure_schema(neo4j_client.driver)
            logger.info("Schema & Index initialization report: %s", report)
    except Exception as exc:
        logger.warning("Storage bootstrap warning on boot: %s", exc)
    yield
    # Shutdown
    try:
        if neo4j_client.driver:
            neo4j_client.driver.close()
    except Exception:
        pass

def create_app() -> FastAPI:
    app = FastAPI(
        title="KryptoDB",
        description="High-Performance Plaintext GraphRAG & Memory Infrastructure",
        version="0.2.0",
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(memory_router)
    app.include_router(admin_router)
    app.include_router(graph_router)
    app.include_router(chat_router)

    # Root route aliases for frontend compatibility
    from kryptodb.interfaces.http.routes.admin import list_agents, get_audit
    from kryptodb.interfaces.http.routes.memory import get_ontology
    from kryptodb.core.retrieval import retrieval_service
    app.add_api_route("/agents", list_agents, methods=["GET"], tags=["Agents"])
    app.add_api_route("/audit", get_audit, methods=["GET"], tags=["Audit"])
    app.add_api_route("/ontology", get_ontology, methods=["GET"], tags=["Ontology"])

    @app.post("/memory/{agent_id}/query", tags=["Legacy Compatibility"])
    async def legacy_memory_query(agent_id: str, req: dict):
        raw_query = req.get("query")
        if isinstance(raw_query, dict):
            query = raw_query.get("query") or raw_query.get("term") or ""
        elif raw_query is not None:
            query = str(raw_query).strip()
        else:
            query = ""
        scope = req.get("requested_scope") or req.get("scope")
        scopes = [scope] if scope else None
        results = retrieval_service.search(query=query, tenant_id="default", active_scopes=scopes)
        facts = []
        for r in results:
            src = r.provenance_path[0].source if r.provenance_path else "Entity"
            pred = r.provenance_path[0].predicate if r.provenance_path else "FACT"
            tgt = r.provenance_path[0].target if r.provenance_path else "Value"
            sc = r.provenance_path[0].scope if r.provenance_path else "default"
            facts.append({
                "id": r.fact_id,
                "subject": src,
                "predicate": pred,
                "object": tgt,
                "summary": r.summary,
                "description": r.summary,
                "confidence": r.confidence,
                "hops": r.hops,
                "citation": r.citation_tag,
                "permission_scope": sc,
            })
        status = "ok" if facts else "not_found"
        return {"status": status, "facts": facts, "mode": req.get("mode", "local")}

    @app.post("/memory", tags=["Legacy Compatibility"])
    @app.post("/memory/remember", tags=["Legacy Compatibility"])
    async def legacy_memory_write(req: dict):
        agent_id = req.get("agent_id", "Agent-1")
        fact_payload = req.get("fact") or req.get("edge") or req
        if not isinstance(fact_payload, dict):
            fact_payload = {}
        subject = fact_payload.get("subject_id") or fact_payload.get("subject") or req.get("subject") or "User"
        predicate = fact_payload.get("predicate") or req.get("predicate") or "HAS_FACT"
        obj = fact_payload.get("object_id") or fact_payload.get("obj") or fact_payload.get("object") or fact_payload.get("target") or req.get("object") or req.get("obj") or "Value"
        scope = fact_payload.get("permission_scope") or fact_payload.get("scope") or req.get("scope") or "default"
        desc = fact_payload.get("description") or req.get("description") or f"{subject} {predicate} {obj}"

        from kryptodb.core.context import RequestContext
        from kryptodb.core.memory.memory_service import memory_service
        ctx = RequestContext.create(tenant_id="default", agent_id=agent_id, active_scopes=[scope, "default", "research"])
        try:
            record = memory_service.propose_memory(
                subject=str(subject).strip(),
                predicate=str(predicate).strip(),
                obj=str(obj).strip(),
                context=ctx,
                description=str(desc),
                scope=scope,
            )
            return {"status": "ok", "fact": record.model_dump(mode="json")}
        except Exception as exc:
            logger.warning("legacy_memory_write error: %s", exc)
            return {"status": "error", "detail": str(exc)}

    # Optional: serve built Next.js frontend if ui-server directory exists
    from pathlib import Path
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    ui_server_dir = Path(__file__).resolve().parent.parent.parent / "ui-server"
    if ui_server_dir.is_dir():
        static_dir = ui_server_dir / ".next" / "static"
        if static_dir.is_dir():
            app.mount("/_next/static", StaticFiles(directory=str(static_dir)), name="next-static")
        public_dir = ui_server_dir / "public"
        if public_dir.is_dir():
            app.mount("/public", StaticFiles(directory=str(public_dir)), name="public")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_frontend(full_path: str):
            index = ui_server_dir / "server" / "app" / "index.html"
            if not index.exists():
                index = ui_server_dir / "server" / "pages" / "index.html"
            if index.exists():
                return FileResponse(str(index))
            fallback = ui_server_dir / "public" / "index.html"
            if fallback.exists():
                return FileResponse(str(fallback))
            return {"status": "ui-server running"}

    return app

app = create_app()
