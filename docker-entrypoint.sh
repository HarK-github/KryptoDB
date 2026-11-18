#!/usr/bin/env sh
# docker-entrypoint.sh — KryptoDB container entrypoint
# Supports two modes:
#   web  (default) — FastAPI daemon + static frontend served at /
#   mcp            — stdio MCP server that connects to $KRYPTODB_URL
set -e

MODE="${1:-web}"

# ── Secret file support ───────────────────────────────────────────────────────
# Read KRYPTODB_ADMIN_API_KEY from a file when KRYPTODB_ADMIN_API_KEY_FILE is set.
# This keeps the raw key out of the environment and process listing.
if [ -n "$KRYPTODB_ADMIN_API_KEY_FILE" ] && [ -f "$KRYPTODB_ADMIN_API_KEY_FILE" ]; then
    KRYPTODB_ADMIN_API_KEY="$(cat "$KRYPTODB_ADMIN_API_KEY_FILE")"
    export KRYPTODB_ADMIN_API_KEY
fi

# ── Production guard ──────────────────────────────────────────────────────────
if [ "${KRYPTODB_ENV:-development}" = "production" ]; then
    if [ -z "$KRYPTODB_MASTER_KEY" ]; then
        echo "FATAL: KRYPTODB_MASTER_KEY is required in production mode." >&2
        exit 1
    fi
    if [ -z "$KRYPTODB_ADMIN_API_KEY" ]; then
        echo "FATAL: KRYPTODB_ADMIN_API_KEY (or KRYPTODB_ADMIN_API_KEY_FILE) is required in production mode." >&2
        exit 1
    fi
fi


case "$MODE" in
    web)
        echo "Starting KryptoDB web daemon (FastAPI + static frontend)..."
        exec uvicorn kryptodb.daemon.app:app \
            --host 0.0.0.0 \
            --port 8000 \
            --workers 1 \
            --no-access-log
        ;;
    mcp)
        echo "Starting KryptoDB MCP server (stdio)..." >&2
        exec python -m kryptodb.interfaces.mcp.server
        ;;
    *)
        echo "Unknown mode: $MODE. Valid values: web, mcp" >&2
        exit 1
        ;;
esac
