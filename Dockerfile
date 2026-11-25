# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Frontend builder
# ─────────────────────────────────────────────────────────────────────────────
FROM node:22.5.1-slim AS frontend-builder

WORKDIR /build/ui

COPY ui/package.json ui/package-lock.json ./
RUN npm ci --ignore-scripts

COPY ui/ ./
ENV NEXT_PUBLIC_KRYPTODB_URL=""
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Python dependency builder (aligned with Python 3.12 + pyproject.toml)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS python-builder

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip build wheel

COPY pyproject.toml ./
RUN mkdir -p kryptodb && touch kryptodb/__init__.py

RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --prefix=/install \
    ".[embeddings]"

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — Runtime image
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="KryptoDB"
LABEL org.opencontainers.image.description="GraphRAG & Multi-Agent Memory Infrastructure on Neo4j"
LABEL org.opencontainers.image.source="https://github.com/prolomaster/KryptoDB"

RUN addgroup --system kryptodb && adduser --system --ingroup kryptodb --no-create-home kryptodb

WORKDIR /app

COPY --from=python-builder /install /usr/local

COPY kryptodb/ ./kryptodb/
COPY data/ ./data/
COPY pyproject.toml ./

RUN pip install --no-cache-dir --no-deps -e .

COPY --from=frontend-builder /build/ui/.next/standalone ./ui-server/
COPY --from=frontend-builder /build/ui/.next/static ./ui-server/.next/static
COPY --from=frontend-builder /build/ui/public ./ui-server/public

RUN mkdir -p /data && chown kryptodb:kryptodb /data
VOLUME ["/data"]

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

USER kryptodb

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
