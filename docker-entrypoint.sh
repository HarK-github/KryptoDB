#!/usr/bin/env sh
# docker-entrypoint.sh — KryptoDB container entrypoint
# Supports two modes:
#   web  (default) — FastAPI daemon + static frontend served at /
#   mcp            — stdio MCP server that connects to $SYNAPSE_URL
set -e

MODE="${1:-web}"

# ── Production guard ──────────────────────────────────────────────────────────
if [ "${SYNAPSE_ENV:-development}" = "production" ]; then
    if [ -z "$SYNAPSE_MASTER_KEY" ]; then
        echo "FATAL: SYNAPSE_MASTER_KEY is required in production mode." >&2
        exit 1
    fi
    if [ -z "$SYNAPSE_ADMIN_API_KEY" ]; then
        echo "FATAL: SYNAPSE_ADMIN_API_KEY is required in production mode." >&2
        exit 1
    fi
fi

case "$MODE" in
    web)
        echo "Starting KryptoDB web daemon (FastAPI + static frontend)..."
        exec uvicorn synapse.daemon.server:app \
            --host 0.0.0.0 \
            --port 8000 \
            --workers 1 \
            --no-access-log
        ;;
    mcp)
        echo "Starting KryptoDB MCP server (stdio)..." >&2
        exec python -m synapse.daemon.mcp_server
        ;;
    *)
        echo "Unknown mode: $MODE. Valid values: web, mcp" >&2
        exit 1
        ;;
esac
