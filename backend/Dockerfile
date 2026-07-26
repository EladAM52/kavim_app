# ══════════════════════════════════════════════════════════════════════════
#  Kavim backend
#  Build context is the REPO ROOT (see infra/docker-compose.yml), so that
#  infra/scripts/ is reachable. Paths below are repo-root relative.
# ══════════════════════════════════════════════════════════════════════════

# ── base ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    TZ=UTC

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# ── dependency layer ──────────────────────────────────────────────────────
# Only the build metadata is copied here, so this layer is cached and rebuilds
# only when dependencies change — not on every source edit. The stub package
# exists solely to satisfy the editable install.
#
# README.md is required, not optional: pyproject.toml declares
# `readme = "README.md"`, and hatchling raises
# `OSError: Readme file does not exist` without it.
FROM base AS deps
COPY backend/pyproject.toml backend/README.md ./
RUN mkdir -p app && touch app/__init__.py \
 && uv pip install --system --no-cache-dir -e ".[dev]"

# ── development ───────────────────────────────────────────────────────────
# docker-compose bind-mounts ../backend over /app, so the COPY below matters
# only for standalone runs and CI.
FROM deps AS dev
COPY backend/ ./
COPY infra/scripts/wait_for_db.sh /usr/local/bin/wait-for-db
RUN chmod +x /usr/local/bin/wait-for-db
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ── production ────────────────────────────────────────────────────────────
# Runtime dependencies only, non-root, no reload.
# The built frontend is copied into app/static by the release pipeline
# (see docs/SPEC.md §5.5) — FastAPI then serves the SPA from the same origin.
FROM base AS prod
COPY backend/pyproject.toml backend/README.md ./
RUN mkdir -p app && touch app/__init__.py \
 && uv pip install --system --no-cache-dir . \
 && rm -rf /root/.cache

COPY backend/ ./
COPY infra/scripts/wait_for_db.sh /usr/local/bin/wait-for-db
RUN chmod +x /usr/local/bin/wait-for-db \
 && groupadd --system --gid 1001 kavim \
 && useradd --system --uid 1001 --gid kavim --no-create-home kavim \
 && mkdir -p /app/.storage \
 && chown -R kavim:kavim /app

USER kavim
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
