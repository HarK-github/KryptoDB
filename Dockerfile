# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Frontend builder
# ─────────────────────────────────────────────────────────────────────────────
FROM node:22.5.1-slim AS frontend-builder

WORKDIR /build/ui

# Install deps from lockfile only (reproducible)
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --ignore-scripts

# Copy source and build in standalone mode
COPY ui/ ./
# NEXT_PUBLIC_SYNAPSE_URL is empty string intentionally:
# the browser hits the same origin (/api/* is proxied by Next.js)
ENV NEXT_PUBLIC_SYNAPSE_URL=""
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Python dependency builder
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11.9-slim AS python-builder

WORKDIR /build

# Install build tools
RUN pip install --no-cache-dir build wheel

# Copy only dependency declaration first for layer caching
COPY pyproject.toml ./
# Stub the package so pip can resolve the install target
RUN mkdir -p synapse && touch synapse/__init__.py

# Install into a prefix we can copy cleanly
RUN pip install --no-cache-dir --prefix=/install \
    fastapi uvicorn[standard] neo4j redis pydantic pydantic-settings \
    cryptography mcp

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Runtime image
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11.9-slim AS runtime

LABEL org.opencontainers.image.title="KryptoDB"
LABEL org.opencontainers.image.description="Crypto-enforced, multi-agent memory substrate"
LABEL org.opencontainers.image.source="https://github.com/prolomaster/KryptoDB"

# Non-root user
RUN addgroup --system kryptodb && adduser --system --ingroup kryptodb --no-create-home kryptodb

WORKDIR /app

# Python packages from builder
COPY --from=python-builder /install /usr/local

# Application source (no .env, no test fixtures, no secrets)
COPY synapse/ ./synapse/
COPY data/ ./data/
COPY pyproject.toml ./

# Install the package in editable mode (uses synapse/ dir, no build artifacts)
RUN pip install --no-cache-dir --no-deps -e .

# Frontend — Next.js standalone build
COPY --from=frontend-builder /build/ui/.next/standalone ./ui-server/
COPY --from=frontend-builder /build/ui/.next/static ./ui-server/.next/static
COPY --from=frontend-builder /build/ui/public ./ui-server/public

# Writable runtime directory for audit archives and app-local state
RUN mkdir -p /data && chown kryptodb:kryptodb /data
VOLUME ["/data"]

# Entrypoint
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

USER kryptodb

# Health check — FastAPI /health endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

# Default: web mode (FastAPI daemon, serves frontend static assets via /ui)
# MCP mode: docker run -i --rm ... kryptodb mcp
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
