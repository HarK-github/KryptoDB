from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from kryptodb.core.context import RequestContext, create_system_context
from kryptodb.core.identity.authentication import authenticator
from kryptodb.infrastructure.audit_ledger import audit_ledger
from kryptodb.infrastructure.neo4j.client import neo4j_client
from kryptodb.infrastructure.neo4j.schema import ensure_schema
from kryptodb.interfaces.http.middleware.context import authorize_or_403, require_context
from kryptodb.interfaces.http.routes.admin import router as admin_router
from kryptodb.interfaces.http.routes.chat import router as chat_router
from kryptodb.interfaces.http.routes.graph import router as graph_router
from kryptodb.interfaces.http.routes.health import router as health_router
from kryptodb.interfaces.http.routes.memory import router as memory_router
from kryptodb.interfaces.http.routes.v1_routes import v1_router

logger = logging.getLogger("kryptodb.app")


def wire_runtime_services(app: FastAPI, storage_mode: str) -> None:
    """Sole composition root: instantiates and wires all concrete adapters into service singletons.

    Called once during lifespan() startup. Reads KRYPTODB_STORAGE_MODE to choose the stack.
    """
    from kryptodb.core.memory.memory_service import memory_service
    from kryptodb.core.retrieval.hybrid_search import configure_hybrid_search, hybrid_search_engine
    from kryptodb.core.retrieval.ranking import path_reranker
    from kryptodb.core.retrieval.service import configure_retrieval_queries, retrieval_service
    from kryptodb.core.retrieval.traversal import configure_traversal
    from kryptodb.infrastructure.embeddings.sentence_transformer import (
        CrossEncoderReranker,
        LocalSentenceEmbedder,
    )
    from kryptodb.infrastructure.neo4j import queries as _q

    allow_ml_fallback = os.getenv("KRYPTODB_USE_REAL_EMBEDDINGS", "0") != "1"
    embedder = LocalSentenceEmbedder(allow_fallback=allow_ml_fallback)
    reranker = CrossEncoderReranker(allow_fallback=allow_ml_fallback)
    hybrid_search_engine.embedder = embedder
    if not reranker._use_fallback and reranker._model is not None:
        path_reranker._cross_encoder = reranker._model
        path_reranker._use_fallback = False

    # Inject Cypher query strings into core/ modules (zero infrastructure imports in core/ at module level)
    configure_hybrid_search(
        client=neo4j_client,
        queries={
            "HYBRID_VECTOR_ENTITY_QUERY": getattr(_q, "HYBRID_VECTOR_ENTITY_QUERY", ""),
            "HYBRID_FULLTEXT_ENTITY_QUERY": getattr(_q, "HYBRID_FULLTEXT_ENTITY_QUERY", ""),
            "SEMANTIC_SEED_FALLBACK_ENTITY_QUERY": getattr(_q, "SEMANTIC_SEED_FALLBACK_ENTITY_QUERY", ""),
            "SEMANTIC_SEED_FALLBACK_FACT_QUERY": getattr(_q, "SEMANTIC_SEED_FALLBACK_FACT_QUERY", ""),
        },
    )
    configure_traversal(
        traversal_1hop=getattr(_q, "TRAVERSAL_1HOP_QUERY", ""),
        traversal_2hop=getattr(_q, "TRAVERSAL_2HOP_EXPANSION_QUERY", ""),
    )
    configure_retrieval_queries(retrospective_query=getattr(_q, "RETROSPECTIVE_FACTS_QUERY", ""))

    if storage_mode == "encrypted":
        _wire_encrypted_stack(app, memory_service, retrieval_service, embedder)
    else:
        _wire_plaintext_stack(app, memory_service, retrieval_service)


def _wire_plaintext_stack(app: FastAPI, memory_service: Any, retrieval_service: Any) -> None:
    """Wire Neo4jMemoryStore + PlaintextNeo4jRetriever into the service singletons."""
    from kryptodb.core.retrieval.hybrid_search import hybrid_search_engine
    from kryptodb.core.retrieval.ranking import path_reranker
    from kryptodb.core.retrieval.traversal import multihop_traverser
    from kryptodb.infrastructure.neo4j.plaintext_retriever import PlaintextNeo4jRetriever
    from kryptodb.infrastructure.neo4j.queries import RETROSPECTIVE_FACTS_QUERY
    from kryptodb.infrastructure.neo4j.store import Neo4jMemoryStore

    store = Neo4jMemoryStore(client=neo4j_client)
    retriever = PlaintextNeo4jRetriever(
        hybrid_engine=hybrid_search_engine,
        traverser=multihop_traverser,
        reranker=path_reranker,
        client=neo4j_client,
        retrospective_query=RETROSPECTIVE_FACTS_QUERY,
    )

    memory_service.configure(store=store, client=neo4j_client)
    retrieval_service.client = neo4j_client
    retrieval_service.retriever = retriever

    app.state.storage_mode = "plaintext"
    logger.info("KryptoDB plaintext storage mode active.")


def _wire_encrypted_stack(app: FastAPI, memory_service: Any, retrieval_service: Any, embedder: Any = None) -> None:
    """Wire EncryptedNeo4jMemoryStore + EncryptedRetrievalService into the service singletons."""
    from kryptodb.core.embeddings.embedder import get_embedder
    from kryptodb.infrastructure.crypto.keyring import LocalKeyFileProvider
    from kryptodb.infrastructure.crypto.ram_vector_index import RAMVectorIndex
    from kryptodb.infrastructure.neo4j.encrypted_retrieval import EncryptedRetrievalService
    from kryptodb.infrastructure.neo4j.encrypted_store import EncryptedNeo4jMemoryStore
    from kryptodb.workers.plaintext_audit import PlaintextAuditScanner

    key_provider = LocalKeyFileProvider()  # fails closed if KRYPTODB_KEY_FILE is missing/invalid
    vector_index = RAMVectorIndex()
    embedder = embedder or get_embedder()
    embed_fn = embedder.embed

    enc_store = EncryptedNeo4jMemoryStore(
        client=neo4j_client,
        key_provider=key_provider,
        vector_index=vector_index,
        embed_fn=embed_fn,
        audit_ledger=audit_ledger,
    )
    enc_store.ensure_schema()

    enc_retrieval = EncryptedRetrievalService(
        store=enc_store,
        vector_index=vector_index,
        embed_fn=embed_fn,
    )

    # Rebuild RAM vector index for configured scopes on startup
    scopes_raw = os.getenv("KRYPTODB_ENCRYPTED_SCOPES", "default,research,engineering")
    sys_ctx = create_system_context()
    for scope in [s.strip() for s in scopes_raw.split(",") if s.strip()]:
        report = enc_retrieval.rebuild_index_for_scope(scope, sys_ctx)
        logger.info("Encrypted RAMVectorIndex rebuilt: %s", report)

    scanner = PlaintextAuditScanner(client=neo4j_client)
    app.state.storage_mode = "encrypted"
    app.state.encrypted_store = enc_store
    app.state.encrypted_retrieval = enc_retrieval
    app.state.plaintext_scanner = scanner

    # Wire services: use enc_retrieval's Retriever-protocol search() method
    memory_service.configure(store=enc_store, client=neo4j_client)
    retrieval_service.client = neo4j_client
    retrieval_service.retriever = enc_retrieval  # enc_retrieval.search() conforms to Retriever

    logger.info("KryptoDB encrypted storage mode active (kid=%s)", key_provider.bundle.current_kid)


async def _plaintext_audit_loop(app: FastAPI, interval_seconds: int) -> None:
    """Periodic background PlaintextAuditScanner loop for encrypted storage mode."""
    scanner = getattr(app.state, "plaintext_scanner", None)
    if scanner is None:
        return
    while True:
        try:
            report = await asyncio.to_thread(scanner.scan)
            app.state.latest_plaintext_audit = {
                "violation_count": report.violation_count,
                "clean": report.clean,
                "last_scan_ts": datetime.now(timezone.utc).isoformat(),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PlaintextAuditScanner background scan failed: %s", exc)
        await asyncio.sleep(max(1, interval_seconds))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Fail-closed admin API key check (zero dev-key fallbacks)
    authenticator.validate_startup_config()

    # 2. Optional JSON policy bootstrap (KRYPTODB_POLICY_FILE)
    loaded = authenticator.load_policy_file()
    if loaded:
        logger.info("Loaded %d agent policy entries from JSON policy file", loaded)

    # 3. Fail-fast Neo4j connectivity & schema bootstrap
    if hasattr(neo4j_client, "connect") and not getattr(neo4j_client, "driver", None):
        neo4j_client.connect()
    if not getattr(neo4j_client, "driver", None):
        raise RuntimeError("Neo4j driver failed to initialize; refusing to start.")
    if hasattr(neo4j_client, "check_health") and not neo4j_client.check_health():
        raise RuntimeError("Neo4j is unreachable on startup; refusing to start.")

    report = ensure_schema(neo4j_client.driver)
    logger.info("Schema & Index initialization report: %s", report)

    # 4. Storage mode wiring via single composition root
    storage_mode = os.getenv("KRYPTODB_STORAGE_MODE", "plaintext").strip().lower()
    wire_runtime_services(app, storage_mode)

    # 5. Start periodic PlaintextAuditScanner in encrypted mode
    audit_task: asyncio.Task[None] | None = None
    if storage_mode == "encrypted" and getattr(app.state, "plaintext_scanner", None) is not None:
        interval = int(os.getenv("KRYPTODB_AUDIT_INTERVAL_SECONDS", "300"))
        audit_task = asyncio.create_task(_plaintext_audit_loop(app, interval))

    try:
        yield
    finally:
        if audit_task is not None:
            audit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audit_task
        try:
            if neo4j_client.driver:
                neo4j_client.driver.close()
        except Exception:
            pass


def _cors_origins() -> list[str]:
    raw = os.getenv("KRYPTODB_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip() and origin.strip() != "*"]


def create_app() -> FastAPI:
    storage_mode = os.getenv("KRYPTODB_STORAGE_MODE", "plaintext").strip().lower()
    desc = (
        "Encrypted GraphRAG & Multi-Agent Memory Infrastructure"
        if storage_mode == "encrypted"
        else "High-Performance GraphRAG & Multi-Agent Memory Infrastructure"
    )
    app = FastAPI(
        title="KryptoDB",
        description=desc,
        version="0.2.0",
        lifespan=lifespan,
    )

    allowed_origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=bool(allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Tenant-ID", "X-Session-ID", "X-User-ID", "X-Trace-ID"],
    )

    from kryptodb.infrastructure.crypto.envelope import DecryptionKeyError

    @app.exception_handler(DecryptionKeyError)
    async def decryption_key_error_handler(request: Request, exc: DecryptionKeyError):
        logger.error("DecryptionKeyError on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Encrypted memory key unavailable or decryption failure threshold exceeded."},
        )

    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(memory_router)
    app.include_router(admin_router)
    app.include_router(graph_router)
    app.include_router(chat_router)

    # Root route aliases for frontend compatibility
    from kryptodb.interfaces.http.routes.admin import get_audit, list_agents
    from kryptodb.interfaces.http.routes.memory import get_ontology
    from kryptodb.core.retrieval import retrieval_service

    app.add_api_route("/agents", list_agents, methods=["GET"], tags=["Agents"])
    app.add_api_route("/audit", get_audit, methods=["GET"], tags=["Audit"])
    app.add_api_route("/ontology", get_ontology, methods=["GET"], tags=["Ontology"])

    @app.post("/memory/{agent_id}/query", tags=["Legacy Compatibility"])
    async def legacy_memory_query(
        agent_id: str,
        req: dict,
        context: RequestContext = Depends(require_context),
    ):
        req_scope = req.get("requested_scope") or req.get("scope")
        scopes = authorize_or_403(
            context,
            "memory:read",
            target_agent_id=agent_id,
            target_scopes=[req_scope] if req_scope else None,
            audit_ledger=audit_ledger,
        )

        raw_query = req.get("query")
        if isinstance(raw_query, dict):
            query = raw_query.get("query") or raw_query.get("term") or ""
        elif raw_query is not None:
            query = str(raw_query).strip()
        else:
            query = ""

        results = retrieval_service.search(
            query=query,
            tenant_id=context.tenant_id,
            active_scopes=scopes,
            now=context.now,
        )
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
        status_str = "ok" if facts else "not_found"
        return {"status": status_str, "facts": facts, "mode": req.get("mode", "local")}

    @app.post("/memory", tags=["Legacy Compatibility"])
    @app.post("/memory/remember", tags=["Legacy Compatibility"])
    async def legacy_memory_write(
        req: dict,
        context: RequestContext = Depends(require_context),
    ):
        requested_agent = req.get("agent_id") or context.agent_id
        fact_payload = req.get("fact") or req.get("edge") or req
        if not isinstance(fact_payload, dict):
            fact_payload = {}
        subject = fact_payload.get("subject_id") or fact_payload.get("subject") or req.get("subject") or ""
        predicate = fact_payload.get("predicate") or req.get("predicate") or ""
        obj = fact_payload.get("object_id") or fact_payload.get("obj") or fact_payload.get("object") or fact_payload.get("target") or req.get("object") or req.get("obj") or ""
        scope = fact_payload.get("permission_scope") or fact_payload.get("scope") or req.get("scope") or "default"
        desc = fact_payload.get("description") or req.get("description") or f"{subject} {predicate} {obj}"

        authorize_or_403(
            context,
            "memory:write",
            target_agent_id=requested_agent,
            target_scopes=[scope],
            audit_ledger=audit_ledger,
        )

        from kryptodb.core.memory.memory_service import memory_service

        ctx = RequestContext.create(
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            active_scopes=[scope],
            is_system=context.is_system,
            role=context.role,
            permissions=context.permissions,
        )
        try:
            record = memory_service.propose_memory(
                subject=str(subject).strip(),
                predicate=str(predicate).strip(),
                obj=str(obj).strip(),
                context=ctx,
                description=str(desc),
                scope=scope,
            )
            audit_ledger.append(
                actor=ctx.agent_id,
                action="propose_memory",
                tenant_id=ctx.tenant_id,
                scope=scope,
                outcome="allowed",
                target_id=record.id,
            )
            return {"status": "ok", "fact": record.model_dump(mode="json")}
        except Exception as exc:
            logger.warning("legacy_memory_write error: %s", exc)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

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
