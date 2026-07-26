# Kavim — backend

FastAPI application, Celery workers, Alembic migrations.

See [`../docs/SPEC.md`](../docs/SPEC.md) for architecture and [`../CLAUDE.md`](../CLAUDE.md) for conventions.

## Run

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload      # http://localhost:8000/docs
```

Requires PostgreSQL and Redis:

```bash
docker compose -f ../infra/docker-compose.yml up -d db redis
```

## Checks

```bash
uv run ruff check . --fix && uv run ruff format .
uv run mypy app
uv run lint-imports          # module boundary rules (.importlinter)
uv run pytest
```

## Layout

```
app/
├── core/           config · database · redis · logging · exceptions · middleware
├── models/         SQLAlchemy ORM, one module per aggregate      (Phase 1)
├── schemas/        Pydantic request/response — the OpenAPI source (Phase 2+)
├── modules/        feature modules; talk via service.py, never routers
├── integrations/   the ONLY place sendgrid/twilio/boto3 are imported
├── workers/        Celery app, beat schedule, background tasks
└── static/         built frontend, mounted in production
```
