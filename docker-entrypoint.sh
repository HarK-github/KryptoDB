#!/usr/bin/env sh
# docker-entrypoint.sh — KryptoDB container entrypoint
# Supports two modes:
#   web  (default) — FastAPI daemon + static frontend served at /
#   mcp            — stdio MCP server that connects to $SYNAPSE_URL
set -e

MODE="${1:-web}"

# ── Secret file support ───────────────────────────────────────────────────────
# Read SYNAPSE_ADMIN_API_KEY from a file when SYNAPSE_ADMIN_API_KEY_FILE is set.
# This keeps the raw key out of the environment and process listing.
if [ -n "$SYNAPSE_ADMIN_API_KEY_FILE" ] && [ -f "$SYNAPSE_ADMIN_API_KEY_FILE" ]; then
    SYNAPSE_ADMIN_API_KEY="$(cat "$SYNAPSE_ADMIN_API_KEY_FILE")"
    export SYNAPSE_ADMIN_API_KEY
fi

# ── Production guard ──────────────────────────────────────────────────────────
if [ "${SYNAPSE_ENV:-development}" = "production" ]; then
    if [ -z "$SYNAPSE_MASTER_KEY" ]; then
        echo "FATAL: SYNAPSE_MASTER_KEY is required in production mode." >&2
        exit 1
    fi
    if [ -z "$SYNAPSE_ADMIN_API_KEY" ]; then
        echo "FATAL: SYNAPSE_ADMIN_API_KEY (or SYNAPSE_ADMIN_API_KEY_FILE) is required in production mode." >&2
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
