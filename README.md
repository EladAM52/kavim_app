# Kavim · קווים

**Production line quality review & task management.**

A Monday.com-style board scoped to manufacturing QA: a line manager opens a quality review, breaks it into tasks and subtasks, assigns workers, and tracks status, dates, and comments in typed columns. Workers join by email invitation, verify with an OTP, and work from a phone on the plant floor.

Hebrew RTL first, English second. Fully responsive — a card list on mobile, a virtualized grid on desktop.

| | |
|---|---|
| Backend | FastAPI · Python 3.12 · SQLAlchemy 2.0 async · Celery |
| Frontend | React 19 · TypeScript · Vite · Tailwind CSS v4 |
| Data | PostgreSQL 16 · Redis 7 |
| Notifications | Gmail SMTP (email) · in-app · SMS deferred |

## Documentation

| Document | Contents |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Master specification — requirements, architecture, services, data model, security, API, roadmap |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Build log — what is done, what is pending, what blocks the next step |
| [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) | Folder tree, creation order, conventions |

## Quick start

Prerequisites: Docker Desktop (WSL 2 backend), Node 20+, Python 3.12, [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. configure
cp .env.example .env          # works as-is for local development

# 2. infrastructure
docker compose -f infra/docker-compose.yml up -d db redis

# 3. backend
cd backend
uv sync
uv run uvicorn app.main:app --reload      # http://localhost:8000

# 4. frontend (new terminal)
cd frontend
npm install
npm run dev                                # http://localhost:5173
```

Verify:

```bash
curl http://localhost:8000/health/ready    # {"status":"ready", ...}
```

- API docs: http://localhost:8000/docs
- App: http://localhost:5173

### Everything in Docker

```bash
docker compose -f infra/docker-compose.yml --profile frontend up
```

Slower on Windows because of bind-mount performance. The hybrid above (infrastructure in Docker, app processes on the host) is the recommended development loop.

## Common commands

```bash
# ── infrastructure ────────────────────────────────────────────────────────
docker compose -f infra/docker-compose.yml up -d db redis
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs -f db
docker compose -f infra/docker-compose.yml down          # stop, keep data

# ── backend ───────────────────────────────────────────────────────────────
cd backend
uv run uvicorn app.main:app --reload
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
uv run pytest
uv run ruff check . --fix && uv run ruff format .
uv run mypy app
uv run lint-imports                        # module boundary rules

# ── frontend ──────────────────────────────────────────────────────────────
cd frontend
npm run dev
npm run build
npm run typecheck
npm run lint
npm run test
npm run api:types                          # regenerate types from OpenAPI
```

> `docker compose down -v` deletes the volumes, which means the entire database. It is the deliberate reset-from-scratch command — never run it against data you care about.

## Project layout

```
kavim/
├── docs/           SPEC.md · PROGRESS.md · adr/
├── backend/        FastAPI app, Celery workers, Alembic migrations
├── frontend/       React SPA (built output is served by FastAPI in production)
├── infra/          docker-compose, Caddy, seed and backup scripts
└── .github/        CI workflows
```

Full annotated tree and creation order: [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md).

## Status

Phase 0 (foundation) — see [`docs/PROGRESS.md`](docs/PROGRESS.md) for the current state and the next step.
