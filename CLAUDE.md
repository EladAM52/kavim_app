# Kavim — working conventions

Read [`docs/SPEC.md`](docs/SPEC.md) before changing architecture, the data model, or the auth flow. It is the contract. Update [`docs/PROGRESS.md`](docs/PROGRESS.md) at the end of every work session.

## What this is

Production-line quality review and task management. Monday.com structure — Project → Task → Subtask, with typed columns — scoped to manufacturing QA. Hebrew RTL primary, English secondary. Mobile-first for workers, desktop for managers.

## Stack

FastAPI (Python 3.12, `uv`) · PostgreSQL 16 · Redis 7 · Celery · React 19 + TypeScript + Vite + Tailwind v4.

**Flask is not used.** See `docs/adr/001-fastapi-only.md`. Do not add a second backend framework.

## Non-negotiables

These are the rules whose violation is expensive to undo. Each one exists because of a decision recorded in `docs/SPEC.md`.

### 1. RTL — logical CSS properties only

```tsx
// ✅  ps-4  pe-2  ms-auto  me-1  start-0  end-0  text-start  text-end  border-s
// ❌  pl-4  pr-2  ml-auto  mr-1  left-0   right-0  text-left  text-right  border-l
```

Physical properties silently break the Hebrew layout. An ESLint rule blocks them. Numbers and dates inside RTL text need `<span dir="ltr">`.

### 2. Never trust the client for authorization

Every mutation endpoint declares `require_permission(...)`. The frontend's `usePermission` hook is a UX affordance only — it hides buttons, it does not secure anything. `tests/security/test_all_routes_declare_permission.py` fails CI if a route omits its declaration.

### 3. Module boundaries

```
core          → imports nothing from modules/ or integrations/
models        → imports only core
schemas       → imports only core, models
modules/X     → may import core, models, schemas, integrations, and modules/Y/service.py
              → MUST NOT import modules/Y/router.py
integrations  → imports only core
workers       → may import anything
```

Enforced by `import-linter` (`backend/.importlinter`) in CI.

### 4. External SDKs live only in `integrations/`

No module imports `sendgrid`, `twilio`, or `boto3` directly. This keeps the app portable and gives tests one place to stub.

### 5. Notifications go through the outbox

Never call a provider or `.delay()` a Celery task from inside a request handler. Write a `notification_outbox` row in the same transaction as the domain change; the sweeper dispatches it. See `docs/adr/005-transactional-outbox.md`.

### 6. Audit every mutation

Call `audit.service.write_audit(...)` inside the same transaction as the change. The application database role has `INSERT`/`SELECT` only on `audit_log` — no `UPDATE`, no `DELETE`.

### 7. Cell writes are versioned

`PATCH /tasks/{id}/cells/{key}` requires `If-Match`. A stale version returns `409` with the current value. Never last-write-wins silently.

### 8. Time

Store `TIMESTAMPTZ` in UTC. Render in `Asia/Jerusalem`. Never store naive datetimes. Never do date arithmetic in local time.

## Conventions

| Kind | Convention |
|---|---|
| Python | `snake_case` files/functions, `PascalCase` classes, 4-space indent, 100-char lines |
| Tables | `snake_case` plural; FKs `<entity>_id`; timestamps `<verb>_at` |
| React | `PascalCase.tsx` components, `useCamelCase.ts` hooks, `camelCase.ts` for the rest |
| i18n keys | `namespace:section.key` — no hardcoded user-facing strings, ever |
| Permissions | `resource:action[:qualifier]` e.g. `task:update:status` |
| API routes | plural nouns, kebab-case: `/api/v1/projects/{id}/columns` |
| WS events | `entity.past_tense` e.g. `cell.changed` |
| Commits | Conventional Commits: `feat(tasks): add fractional index reordering` |
| Branches | `type/short-description` |

## Adding things

| Adding… | Touch |
|---|---|
| A column type | `modules/projects/columns.py` (validation) + `components/board/cells/` (editor) + register in `cells/index.ts` |
| A notification trigger | `modules/notifications/service.py` + `templates/<event>/` with **both** `he` and `en` copy |
| A permission | `core/permissions.py` registry + seed migration + `features/admin/RoleMatrix` |
| An external provider | `integrations/` only |
| A scheduled job | `workers/tasks_*.py` + `workers/beat_schedule.py` |
| An env var | `core/config.py` **and** `.env.example` **and** the table in `docs/SPEC.md` §12.1 |

## Testing

Integration tests run against real PostgreSQL via `testcontainers` — never SQLite. JSONB, GIN, `CITEXT`, and `SKIP LOCKED` behave differently, so SQLite would validate the wrong database.

Coverage floor: 80% overall, 90% on `auth` and `permissions`.

## Before finishing any change

```bash
cd backend  && uv run ruff check . && uv run mypy app && uv run lint-imports && uv run pytest
cd frontend && npm run typecheck && npm run lint && npm run test
```

Then update `docs/PROGRESS.md`: what changed, what is left, what blocks the next step.
