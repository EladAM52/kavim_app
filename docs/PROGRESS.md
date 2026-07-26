# Kavim — Build Log

Running record of what has been built, what was decided, what broke, and what must be done before the next step.

**How this document works.** One section per phase, appended as work happens. Each entry states what was delivered, what was verified (with evidence, not assertion), what went wrong, and what is outstanding. The **Next step** section at the bottom is always current — read that first.

| | |
|---|---|
| Spec | [`SPEC.md`](SPEC.md) |
| Structure | [`../PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) |
| Conventions | [`../CLAUDE.md`](../CLAUDE.md) |
| Last updated | 2026-07-26 |

---

## Status at a glance

| Phase | Scope | State |
|---|---|---|
| **0** | Foundation: repo, Docker, `core`, health endpoints, React shell with RTL/i18n, CI | ✅ **Complete and verified** |
| 1 | Data model, Alembic migrations, seed script | ⬜ Next |
| 2 | Auth: invite → OTP → register → login → refresh | ⬜ |
| 3 | RBAC + admin panel | ⬜ |
| 4 | Projects, groups, column engine | ⬜ |
| 5 | Tasks, subtasks, cell editing, drag-drop | ⬜ |
| 6 | Comments, attachments, WebSocket live updates | ⬜ |
| 7 | Notifications: SendGrid + Twilio + preferences | ⬜ Blocked on provider setup (see below) |
| 8 | Mobile card view, PWA, reports, RTL polish | ⬜ |

---

## Session 1 — 2026-07-26

### Decisions made

| Decision | Outcome | Rationale |
|---|---|---|
| **System name** | **Kavim** (קווים, "lines"). Code identifier `kavim` | Chosen by the user over QualiLine. Hebrew-native and immediately meaningful to plant staff, which matters because Hebrew is the primary UI language |
| Backend framework | FastAPI only — **Flask dropped** | Flask would duplicate auth, config, middleware, and error formatting for no capability gain. `docs/adr/001` |
| Frontend | React 19 + TypeScript + Vite, separate `frontend/`, built output served by FastAPI in production | The product is an interactive editable grid; server-rendered templates would mean hand-writing all of it |
| Database | PostgreSQL 16 | JSONB custom columns, recursive CTE for the task tree, `tsvector` search, `LISTEN/NOTIFY`, `SKIP LOCKED`, RLS available later. Managed by every cloud, so the cloud move is a connection-string change |
| Column storage | Hybrid: hot fields as real indexed columns, user-defined fields in one GIN-indexed `JSONB` column described by `board_columns` | Pure EAV needs 7500 rows to render one 500-task board; `ALTER TABLE` per user action puts DDL in end users' hands |
| Dev environment | Docker Compose for Postgres + Redis; app processes on the host | Bind mounts are slow on Windows, so containerised app processes sit behind the `app` compose profile |
| Cloud target | Cloud-agnostic containers, decided later | Nothing provider-specific outside `app/integrations/` |
| Language | Hebrew RTL primary, English LTR | Retrofitting RTL onto a finished data grid costs multiples of building it in |
| Scale | One line, under 50 workers. `site_id`/`line_id` present so multi-line is additive | |
| Tailwind config style | CSS-first `@theme` in `src/styles/index.css`, **no `tailwind.config.ts`** | Tailwind v4 is CSS-first; a JS config file would be the legacy path. Differs from the original plan in `PROJECT_STRUCTURE.md` |

### Environment prepared

Docker Desktop was not installed at the start of the session.

- Diagnosed the "Virtualization support not detected" error as **misleading**: firmware virtualization was already enabled (`VirtualizationFirmwareEnabled: True`) and VBS/Credential Guard was actively running. The real cause was that `VirtualMachinePlatform` and WSL were not enabled.
- Fix applied by the user: `wsl --install` in an elevated shell.
- **No reboot was needed** — `VirtualMachinePlatform` came up `Enabled` and the daemon was reachable immediately.
- Confirmed WSL 1 is irrelevant here: `Microsoft-Windows-Subsystem-Linux` remains `Disabled` and that is correct, it is the legacy WSL 1 component.

Verified toolchain: Docker 29.6.2 (WSL 2 backend, 16 CPUs, 11.5 GB), Compose v5.3.1, Python 3.12.2, Node 24.12.0, npm 11.6.2, `uv`, git.

### Delivered — documentation

| File | Contents |
|---|---|
| `docs/SPEC.md` | 15 sections: naming, actors and glossary, ~70 numbered `FR-###`, 22 `NFR-##`, Mermaid architecture diagrams, 14 service definitions, ERD and DDL, full auth flow and security controls, API contract, frontend/RTL/responsive strategy, testing, operations, roadmap, risks, 6 ADRs |
| `PROJECT_STRUCTURE.md` | Annotated tree, 17-step creation order with the dependency reason for each, naming conventions, boundary rules |
| `docs/PROGRESS.md` | This document |
| `README.md` | Five-command quick start, common commands |
| `CLAUDE.md` | Eight non-negotiables, conventions, "where new things go" |

### Delivered — infrastructure

- `infra/docker-compose.yml` — `db` (postgres:16-alpine) and `redis` (redis:7-alpine) with healthchecks; `backend`/`worker`/`beat` behind the `app` profile; `frontend` behind the `frontend` profile. Named volumes for data.
- `infra/postgres/init/01-extensions.sql` — creates `pgcrypto`, `citext`, `pg_trgm`, `btree_gin` on first init, so Alembic never needs superuser rights.
- `infra/scripts/wait_for_db.sh` — entrypoint guard for the cases compose healthchecks do not cover.
- `.env.example` — every variable the application reads, with development-safe defaults.
- `backend/Dockerfile` — `base` → `deps` → `dev`/`prod`. Dependencies install in their own layer so a source edit does not reinstall them. `prod` runs non-root with a healthcheck.
- `frontend/Dockerfile` — `deps` → `dev`/`build`/`dist`.

### Delivered — backend

`app/core/` is complete:

| Module | Notes |
|---|---|
| `config.py` | Typed `pydantic-settings`. A production-grade guard refuses to start if `SECRET_KEY` is the placeholder or under 32 chars, `APP_DEBUG` or `DATABASE_ECHO` is on, `APP_BASE_URL` is plaintext HTTP, SendGrid is enabled while still in sandbox, or storage is `local` |
| `database.py` | Async engine, `pool_pre_ping`, `pool_recycle`, `statement_cache_size=0` for pooler compatibility. `get_db` commits on clean return and rolls back on any exception — the mechanism behind the "domain change, history row, audit row, and outbox row all commit together" guarantee |
| `redis.py` | Pooled client plus cache helpers that fail soft: Redis being down degrades performance, never correctness. `cache_delete_prefix` uses `SCAN`, not `KEYS` |
| `logging.py` | structlog, JSON in production, `request_id`/`user_id` contextvars on every line |
| `exceptions.py` | Full error hierarchy rendered as RFC 7807 `application/problem+json`, including `VersionConflictError` carrying the current value for the 409 conflict UI |
| `middleware.py` | Request id (echoed, length-capped), timing, security headers, CSP strict in production and relaxed for the Vite dev server, CORS only when origins are configured |

Also: `app/main.py` (app factory, `/health/live`, `/health/ready`, `/api/v1/` meta, SPA mount with a path-containment check), `app/workers/celery_app.py` (JSON-only serialization — never pickle; `acks_late`; request-id propagation into tasks), package skeletons for `models`/`schemas`/`modules`/`integrations`, and `.importlinter` with 4 enforced contracts.

### Delivered — frontend

- Vite + React 19 + TS strict (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`). Split `tsconfig.app.json` / `tsconfig.node.json` so Node globals cannot leak into browser code.
- **RTL foundation** — `lib/rtl.ts` (direction resolution, regional-tag handling, `physicalToLogical`, `inlineSign` for drag deltas), `hooks/useDirection`, Radix `DirectionProvider`, `<html dir>` set *before* first paint so there is no visible flip on load.
- **The ESLint rule that bans physical CSS utilities** — `pl-`, `ml-`, `text-left`, `border-l`, `rounded-r`, `left-`, `float-left`, in `className` strings, template literals, and `clsx`/`cn` calls.
- i18n: i18next with `common` and `errors` namespaces in Hebrew and English, `load: 'languageOnly'` so `he-IL` resolves to `he`.
- `api/client.ts` — typed problem+json parsing, `ApiError` with a stable `code` for branching and a `translationKey` for display, `If-Match` and `Idempotency-Key` support, and a marked seam for Phase 2 auth.
- `AppShell` + `SystemStatus` — the Phase 0 acceptance screen. Design tokens in CSS `@theme`, including a 44px touch-target token and status colours that never convey state by colour alone.
- Tests: 15 passing (12 RTL logic, 3 `LanguageToggle` including a real `<html dir>` flip assertion).

### Problems found and fixed

Four real defects, all caught by running the code rather than by reading it.

1. **`SUPPORTED_LOCALES=he,en` crashed startup.** pydantic-settings JSON-decodes list-typed fields at the *source* level, before validators run, so a `BeforeValidator` never got a chance. Fixed with the `NoDecode` annotation. The original test passed values as init kwargs, which does not exercise the env-var path — a regression test using real environment variables was added.
2. **`AttributeError: 'WriteLogger' object has no attribute 'name'` on startup.** `structlog.stdlib.add_logger_name` requires a stdlib-backed logger, but the factory was `WriteLoggerFactory`. Split into two processor chains: native processors for structlog loggers, `structlog.stdlib.*` only in the `foreign_pre_chain` where records really do come from stdlib. Six logging tests added, since this class of failure surfaces at first log call — meaning at startup, not at import.
3. **RTL lint rule had a false positive.** `rounded-l` matched inside `rounded-lg`, flagging correct code. Fixed with a `(?![a-z])` guard, then verified both directions with a throwaway probe file: physical utilities are rejected, logical utilities pass.
4. **Two clashing Vite type identities.** `vitest@2` peers on Vite 5, so npm nested a second copy under `node_modules/vitest/`, producing an unreadable wall of "Type `Plugin<any>` is not assignable to type `Plugin<any>`". Fixed by moving to `vitest@3`, which supports Vite 6. Confirmed deduped.

Also cleaned up: FastAPI now serializes JSON directly, so `ORJSONResponse` and the `orjson` dependency were removed; `HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`.

### Verification evidence

Backend quality gate — all clean:

```
ruff          All checks passed!
ruff format   20 files unchanged
mypy --strict Success: no issues found in 16 source files
import-linter Contracts: 4 kept, 0 broken
pytest        25 passed
```

Containers healthy and extensions present:

```
kavim-db      running   Up (healthy)
kavim-redis   running   Up (healthy)

extname: btree_gin, citext, pg_trgm, pgcrypto, plpgsql
```

**Phase 0 acceptance criterion met** — `/health/ready` returns 200 with both dependencies reachable:

```json
{"status":"ready","version":"0.1.0","environment":"development",
 "checks":{"database":"ok","redis":"ok"}}
```

Frontend gate — all clean:

```
tsc -b            (no errors)
eslint .          (no errors)
prettier --check  All matched files use Prettier code style!
vitest run        15 passed
vite build        built in 914ms
```

Bundle size, gzipped: `index` 69.77 kB + `i18n` 17.56 kB + `query` 14.80 kB + `react` 1.55 kB + CSS 4.16 kB ≈ **108 kB**, against the 250 kB budget in `NFR-02`.

Full chain verified through the Vite proxy — browser → Vite → FastAPI → Postgres/Redis:

```
GET http://localhost:5173/              → <html lang="he" dir="rtl"> <title>Kavim</title>
GET http://localhost:5173/health/ready   → {"status":"ready", ...database:"ok", redis:"ok"}
GET http://localhost:5173/api/v1/        → {"name":"Kavim", ..., "default_locale":"he"}
```

### Known gaps left deliberately

| Gap | Why, and when it closes |
|---|---|
| Coverage gate not enforced in CI | Phase 0 has little to cover. `fail_under = 80` is configured and starts being enforced in Phase 2, when `auth` and `permissions` exist |
| Migration round-trip step is conditional | Skips itself until `alembic/versions/` is non-empty — Phase 1 |
| Cross-module router import contract absent from `.importlinter` | import-linter errors on modules that do not exist yet. Added in Phase 2 alongside the first routers |
| PWA icons referenced by `manifest.webmanifest` do not exist | Only `favicon.svg` is present. PNG icons land with the PWA work in Phase 8 |
| Self-hosted Heebo/Inter fonts | System font stack for now; Phase 8 |
| Nothing committed yet | `git init -b main` was run and `.gitignore` verified (`.env`, `node_modules/`, `.venv/`, `.idea/` all ignored), but no commit has been made — awaiting the go-ahead. CI's `npm ci` needs `frontend/package-lock.json` in that first commit |

---

## Blocked / awaiting external action

These have lead times and are **not** blocked on development. Starting them now keeps them off the critical path.

| # | Item | Blocks | Owner | Notes |
|---|---|---|---|---|
| E1 | **SendGrid account + domain authentication** (SPF + DKIM DNS records) | Phase 7; full email testing in Phase 2 | User + IT | The DNS step needs IT and takes days. Development is unblocked meanwhile because `SENDGRID_SANDBOX=true` sends nothing |
| E2 | **Twilio account + Israeli sender registration** | Phase 7 SMS | User | Regulated; multi-day. Test credentials work immediately for development |
| E3 | Decision: is **Entra ID SSO** expected within 12 months? | Phase 2 design | User | Changes how much to invest in the password flow. The model keeps `auth_provider` / `external_idp_id` either way |
| E4 | Decision: required **retention period for quality records** | Audit and soft-delete design | User + QA/compliance | Currently assumed 24 months |
| E5 | **Existing quality checklists or forms** (photo or Excel of a real one) | Phase 4 default column set and templates | User | Highest-value input available. Turns generic templates into ones that are immediately useful |
| E6 | **Pilot cohort**: which line, which shift, how many workers | Phase 8 scope | User | |
| E7 | **Plant Wi-Fi coverage measurement** at the stations workers use | Phase 8 offline scope | User | Determines whether the offline queue needs to be more or less capable than currently planned |

---

## Next step — Phase 1: Data model

Nothing external blocks this.

**To do**

1. First commit (repo is initialized; `main.py` boilerplate already removed). Include `frontend/package-lock.json`.
2. `app/models/base.py` — `DeclarativeBase`, `TimestampMixin`, `SoftDeleteMixin`, UUID primary-key default.
3. All ORM models per `SPEC.md` §7: `sites`, `lines`, `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `invitations`, `otp_codes`, `refresh_tokens`, `projects`, `project_members`, `groups`, `board_columns`, `tasks`, `task_assignees`, `task_cell_history`, `task_dependencies`, `comments`, `attachments`, `notification_outbox`, `notification_deliveries`, `notification_preferences`, `audit_log`, `saved_views`.
4. Alembic setup (`alembic.ini`, `env.py` wired to the async engine) plus one complete initial migration — easier to review as one migration than as fifteen during initial design.
5. Indexes from §7.2, including the `tasks.custom` GIN index and the partial indexes that exclude soft-deleted rows.
6. Grant the application role `INSERT`/`SELECT` only on `audit_log` — no `UPDATE`, no `DELETE`, so the trail cannot be rewritten by application code.
7. `infra/scripts/seed.py` — demo site and line, one user per role, a realistic ~40-task hygiene-audit project with custom columns, groups, subtasks, and comments.
8. `tests/conftest.py` integration fixtures using `testcontainers-postgres` (real Postgres, never SQLite — JSONB, GIN, `CITEXT`, and `SKIP LOCKED` all differ), plus `factory-boy` factories.
9. Enable the migration round-trip step in CI.

**Done when**

- `alembic upgrade head` → `downgrade base` → `upgrade head` round-trips cleanly
- The seeded demo board is queryable and its shape matches the ERD in `SPEC.md` §7
- Integration tests run green against a real Postgres container

**Currently running in this session** (background processes, safe to stop)

```
uvicorn app.main:app --host 127.0.0.1 --port 8000     # backend
npm run dev  (frontend, http://localhost:5173)        # Vite
docker compose -f infra/docker-compose.yml up -d db redis
```
