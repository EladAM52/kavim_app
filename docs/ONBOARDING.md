# Kavim — Onboarding

Orientation for someone joining the codebase, or returning to it after a break. Written for a
reader who has not used Docker, FastAPI, or React + Vite before.

This is a **teaching** document. It explains what each part is *for* and how the pieces fit
together. It deliberately does not restate the other documents:

| Document | What it is | When to read it |
|---|---|---|
| [`SPEC.md`](SPEC.md) | The contract. Requirements, data model, auth flow, API surface | Before changing architecture, the data model, or auth |
| [`PROGRESS.md`](PROGRESS.md) | The build log. What was delivered, verified, broken, outstanding | **First**, every session. The "Next step" section at the bottom is always current |
| [`../PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) | The target tree, and the order things get built in | When deciding where a new file goes |
| [`../CLAUDE.md`](../CLAUDE.md) | Working conventions and the eight non-negotiable rules | Before writing any code |

If those two sentences of guidance conflict with anything here, they win. This file is a map,
not a source of truth.

---

## 1. The concepts, briefly

Skip this section if Docker, FastAPI, and Vite are already familiar.

### Docker and Docker Compose

A **container** runs a program together with its whole operating-system-level environment,
isolated from your machine. It is built from an **image**, and an image is built from a
`Dockerfile` — a recipe that says "start from Python 3.12, install these packages, copy this
code, run this command".

The practical benefit here: nobody installs PostgreSQL or Redis on Windows. They run in
containers, identical on every developer machine and in CI.

**Docker Compose** describes several containers at once in one YAML file and starts them
together, on a shared private network where they can reach each other by service name.

In this project, [`infra/docker-compose.yml`](../infra/docker-compose.yml) defines five
services. Only two start by default:

```bash
docker compose -f infra/docker-compose.yml up -d db redis
```

The application processes (`backend`, `worker`, `beat`) sit behind the `app` profile and the
Vite dev server behind the `frontend` profile, so they do *not* start unless you ask for them.
That is deliberate: bind-mounting source code into a container is slow on Windows, so the day
to day loop runs Python and Node directly on the host and only the data layer in Docker.

Two Docker details that come up constantly:

- **Healthchecks.** A container can be "running" while the program inside is still starting.
  A healthcheck is a command Docker runs periodically to decide whether the service is
  actually usable. `depends_on: { condition: service_healthy }` makes one service wait for
  another to pass its healthcheck.
- **Volumes.** Container filesystems are thrown away on removal. A named volume (`pgdata`,
  `redisdata`) persists data across restarts. `docker compose down -v` deletes them — that is
  how you reset the database completely.

### FastAPI, ASGI, and uvicorn

**FastAPI** is a Python web framework. You write async functions and decorate them with the
HTTP method and path:

```python
@health_router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "alive", "version": __version__}
```

FastAPI reads the type annotations to validate input, serialize output, and generate an
OpenAPI schema. In development that schema is browsable at <http://localhost:8000/docs> — an
interactive page listing every endpoint, which you can call directly from the browser. It is
disabled in production (see [`app/main.py`](../backend/app/main.py)).

**ASGI** is the interface between a Python web framework and a web server, the async successor
to WSGI. **uvicorn** is the ASGI server: the process that actually binds port 8000 and speaks
HTTP. FastAPI is your code; uvicorn runs it.

```bash
cd backend && uv run uvicorn app.main:app --reload
```

`app.main:app` means "the object named `app` in the module `app/main.py`". `--reload` restarts
on file changes.

**`uv`** is the package manager, a fast replacement for `pip` + `venv`. `uv run <cmd>` runs a
command inside the project environment, creating it from `uv.lock` if needed. You never need
to activate a virtualenv manually.

**Dependency injection** is FastAPI's main idiom and worth understanding early. A parameter
declared as `db: AsyncSession = Depends(get_db)` tells FastAPI to call `get_db()` and pass the
result. In this codebase `get_db` (in [`core/database.py`](../backend/app/core/database.py))
opens a transaction, yields the session, then commits on clean return or rolls back on any
exception. That single behaviour is what makes the promise in `CLAUDE.md` rule 5 and 6
possible: the domain change, the audit row, and the outbox row all commit together or not at
all.

### PostgreSQL, SQLAlchemy, and Alembic

**SQLAlchemy** maps Python classes to database tables. A model class in
[`app/models/`](../backend/app/models/) declares columns, constraints, and relationships; the
ORM turns attribute access into SQL.

**Alembic** versions the schema. Every schema change is a Python file with an `upgrade()` and
a `downgrade()`, applied in order:

```bash
uv run alembic upgrade head      # apply everything
uv run alembic downgrade base    # undo everything
```

Both directions are reviewed here, and CI runs the full round-trip, because a `downgrade` that
has never been executed is a `downgrade` that does not work — and you find that out during an
incident.

### Redis and Celery

**Redis** is an in-memory key-value store. Two roles here: a cache, and the message broker
between the web process and background workers. The cache helpers in
[`core/redis.py`](../backend/app/core/redis.py) **fail soft** — if Redis is down the app is
slower, never wrong.

**Celery** runs work that is too slow for an HTTP request: sending email, generating exports,
building thumbnails. The web process writes a job, a separate `worker` process picks it up.
`beat` is Celery's scheduler — the cron table lives in
[`workers/beat_schedule.py`](../backend/app/workers/beat_schedule.py).

Important project rule (`CLAUDE.md` rule 5): a request handler **never** calls `.delay()` and
never talks to an email or SMS provider directly. It writes a `notification_outbox` row in the
same transaction as the domain change, and a sweeper dispatches it. If the transaction rolls
back, the notification was never queued — which is the whole point.

### React, TypeScript, and Vite

**React** builds UI from components: functions that return JSX, an HTML-like syntax. State
changes re-run the function and React updates only the parts of the DOM that differ.

**TypeScript** adds types to JavaScript, checked at compile time and erased at runtime.

**Vite** is the build tool and dev server. In development it serves modules to the browser
natively with near-instant hot reload; for production `vite build` bundles everything into
static files.

The single most useful thing to know about the Vite setup here is the **dev proxy**, in
[`vite.config.ts`](../frontend/vite.config.ts):

```ts
proxy: {
  '/api':    { target: API_TARGET, changeOrigin: true },
  '/ws':     { target: API_TARGET, ws: true, changeOrigin: true },
  '/health': { target: API_TARGET, changeOrigin: true },
}
```

The browser only ever talks to `localhost:5173`. Vite forwards anything under those prefixes
to FastAPI on `:8000`. So frontend code writes `fetch('/api/v1/...')` with no base URL and no
environment branching — and in production, where FastAPI serves the built SPA from the same
origin, the exact same code works with no CORS involved at all.

Two supporting libraries you will meet immediately:

- **TanStack Query** caches server state. `useQuery` handles loading, error, retry, and
  refetch. Configured in [`App.tsx`](../frontend/src/App.tsx) with a 30-second stale time and
  no refetch-on-focus, because plant-floor Wi-Fi is unreliable and stale data beats a spinner.
- **i18next** handles translations. Every user-facing string is a key like
  `system.database`, resolved from [`src/locales/`](../frontend/src/locales/). There are no
  hardcoded strings, ever.

---

## 2. How it fits together at runtime

### Development

```
Browser :5173
    │
    ▼
Vite dev server ──── proxy /api /ws /health ────► uvicorn :8000  (FastAPI)
(host process)                                    (host process)
                                                        │
                                        ┌───────────────┴───────────────┐
                                        ▼                               ▼
                                 PostgreSQL :5432                 Redis :6379
                                 (docker container)               (docker container)
                                                                        │
                                                                        ▼
                                                          Celery worker + beat
                                                          (not yet running; Phase 7)
```

### Production

One origin, one port. The release pipeline runs `vite build` and copies `frontend/dist` into
`backend/app/static`. FastAPI then serves the SPA itself — see `_mount_spa` in
[`app/main.py`](../backend/app/main.py). No CORS, no second web server, and deep links such as
`/projects/<id>/board` survive a hard refresh because any unmatched path falls back to
`index.html` (with a path-containment check so nothing outside the static directory can be
served).

### The two health endpoints, and why there are two

- `GET /health/live` — "the process is up". Deliberately checks nothing else. If it checked
  the database, a 5-second Postgres blip would make the orchestrator kill and restart a
  perfectly healthy container.
- `GET /health/ready` — "this instance can take traffic". Checks PostgreSQL and Redis, returns
  `503` if either is unreachable. That is what makes a load balancer route around it rather
  than serve errors.

`/health/ready` is also the fastest way to confirm your whole local stack is wired up:

```json
{"status":"ready","version":"0.1.0","environment":"development",
 "checks":{"database":"ok","redis":"ok"}}
```

---

## 3. Map of the repository

Roughly 95 tracked files. Below is what each one is *for*. Folders listed in
[`PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) but absent from disk have not been built yet
— see §5.

### Root

| File | Role |
|---|---|
| `README.md` | Five-command quick start |
| `CLAUDE.md` | Conventions and the eight non-negotiable rules |
| `PROJECT_STRUCTURE.md` | Target tree, creation order, naming |
| `.env.example` | Every variable the app reads, with development-safe defaults. Copy to `.env` |
| `.editorconfig` | Editor indentation and newline settings |
| `.gitattributes` | Line-ending normalization — matters on Windows |
| `.gitignore` | `.env`, `node_modules/`, `.venv/`, `__pycache__`, `dist/` |
| `.gitleaks.toml` | Secret-scanner configuration |
| `.github/workflows/ci.yml` | Four CI jobs: backend, frontend, security, docker build |

### `infra/` — the local stack

| File | Role |
|---|---|
| `docker-compose.yml` | `db` and `redis` by default; `backend`/`worker`/`beat` behind the `app` profile; `frontend` behind the `frontend` profile |
| `postgres/init/01-extensions.sql` | Creates `pgcrypto`, `citext`, `pg_trgm`, `btree_gin` on first database init, so Alembic never needs superuser rights |
| `scripts/wait_for_db.sh` | Entrypoint guard for cases Compose healthchecks do not cover |

PostgreSQL is initialized with `--locale=C` so `ORDER BY` produces identical results on every
machine.

### `backend/` — build and tooling

| File | Role |
|---|---|
| `pyproject.toml` | Dependencies plus all tool configuration: `ruff` (lint + format, 100-char lines, security and timezone rule sets enabled), `mypy` strict, `pytest`, `coverage` (`fail_under = 80`) |
| `uv.lock` | Exact pinned dependency versions |
| `.importlinter` | Four machine-enforced architecture contracts (see below) |
| `Dockerfile` | Stages `base` → `deps` → `dev` / `prod`. Dependencies install in their own cached layer so a source edit does not reinstall them; `prod` runs non-root with a healthcheck |
| `alembic.ini` | Migration tool configuration |

**The import contracts are worth understanding**, because they are the thing keeping this a
modular monolith rather than a ball of mud. `uv run lint-imports` enforces:

```
core          → imports nothing from modules/, schemas/, integrations/, or workers/
models        → imports only core
schemas       → imports only core, models
modules/X     → may import core, models, schemas, integrations, and modules/Y/service.py
              → MUST NOT import modules/Y/router.py
integrations  → imports only core
workers       → may import anything (outermost layer)
```

Plus one more: `sendgrid`, `twilio`, and `boto3` may be imported **only** inside
`app/integrations/`. That keeps the application portable and gives tests one place to stub.

### `backend/app/core/` — the substrate every module imports

| File | Role |
|---|---|
| `config.py` | Typed settings from environment variables. A production guard refuses to start if `SECRET_KEY` is the placeholder or under 32 characters, debug or SQL echo is on, the base URL is plaintext HTTP, SendGrid is still in sandbox, or storage is `local` |
| `database.py` | Async engine and the `get_db` dependency described in §1 |
| `redis.py` | Pooled client and fail-soft cache helpers. Uses `SCAN`, never `KEYS` |
| `logging.py` | structlog. JSON in production, with `request_id` / `user_id` on every line |
| `exceptions.py` | Error hierarchy rendered as RFC 7807 `application/problem+json`, including `VersionConflictError`, which carries the current value so the frontend can show a real conflict dialog |
| `middleware.py` | Request id, timing, security headers, CSP (strict in production, relaxed for the Vite dev server), CORS only when origins are configured |
| `enums.py` | All shared enumerations. Lives in `core`, not `models`, because `core.permissions` needs `RoleKey` and the layering forbids `core → models` |
| `permissions.py` | 30 permission strings, the seeded role matrix, and `resolve_effective_permissions`. Data only — the `require_permission` dependency arrives in Phase 3 |
| `security.py` | argon2id password hashing with transparent rehash, SHA-256 token and OTP digests, `secrets`-based generation, constant-time comparison, and `waste_password_time()` so an unknown email is indistinguishable from a known one in timing as well as response |
| `time.py` | `utc_now`, `local_today`, `start_of_local_day`, `is_within_quiet_hours`. Exists because `date.today()` reads the *process* timezone — on a UTC server at 01:00 Jerusalem time it returns yesterday, and an overdue scan would fire a day early |

### `backend/app/main.py`

The application factory. Registers middleware and exception handlers, mounts the health
router, the `/api/v1` router (currently just a meta endpoint advertising locales and
timezone), and the SPA fallback. Feature routers mount here as each module lands.

### `backend/app/models/` — 27 tables in 13 files

`base.py` provides the declarative base and the shared building blocks:

- A **constraint naming convention.** Without it PostgreSQL invents index and constraint
  names, Alembic autogenerate cannot match them against the models, and `downgrade` fails on
  names it never knew.
- `uuid_pk()` — UUID primary keys generated by the database via `gen_random_uuid()`, so rows
  created by raw SQL (the seed script, data migrations) get valid ids without the ORM.
- `enum_type()` — `VARCHAR` + `CHECK` rather than a native PostgreSQL `ENUM`, because adding a
  value to a native enum needs `ALTER TYPE`, which complicates expand/contract deploys.
- Mixins: `UUIDPrimaryKeyMixin`, `TimestampMixin`, `SoftDeleteMixin`.

| File | Tables |
|---|---|
| `site.py` | `sites`, `lines` |
| `user.py` | `users`, `notification_preferences` |
| `role.py` | `roles`, `permissions`, `role_permissions`, `user_roles` |
| `auth.py` | `invitations`, `otp_codes`, `refresh_tokens`, `password_reset_tokens` |
| `project.py` | `projects`, `project_members`, `groups`, `saved_views` |
| `column.py` | `board_columns` |
| `task.py` | `tasks`, `task_assignees`, `task_cell_history`, `task_dependencies` |
| `comment.py` | `comments` |
| `attachment.py` | `attachments` |
| `notification.py` | `notification_outbox`, `notification_deliveries`, `in_app_notifications` |
| `audit.py` | `audit_log` |

Three schema decisions that will surprise you if you have not seen them:

1. **`search_vector` is a generated column**, maintained by PostgreSQL. It cannot drift from
   the source text, and there is no trigger or application code to forget.
2. **Every relationship uses `lazy="raise_on_sql"`.** Under async SQLAlchemy an implicit lazy
   load raises `MissingGreenlet` at runtime. Making it raise at development time instead
   forces an explicit `selectinload` where one is needed.
3. **`audit_log` is append-only, enforced by a trigger**, not a `GRANT`. In development the
   application connects as the table owner, and an owner always retains full privileges — so a
   grant alone would not hold. A trigger holds for every role, including a buggy ORM call,
   which is the real threat model. Retention pruning opts in explicitly with
   `SET LOCAL kavim.audit_maintenance = 'on'`.

### `backend/alembic/`

`env.py` wires the async engine and imports every model. `versions/` currently holds one
migration, `20260726_1330_f81eb8b34800_initial_schema.py`, creating all 27 tables with their
indexes, constraints, functions, and triggers. One complete migration is cleaner to review
than fifteen incremental ones written during initial design.

### `backend/app/` — the rest

| Path | Role |
|---|---|
| `scripts/seed.py` | Reference data plus a realistic demo board in Hebrew. Idempotent. Lives on the package path because it imports the application, so `python -m app.scripts.seed` works unchanged inside the container |
| `workers/celery_app.py` | Broker configuration. JSON-only serialization (never pickle), `acks_late`, request-id propagation into tasks |
| `workers/beat_schedule.py` | The cron table |
| `modules/`, `schemas/`, `integrations/` | Empty package skeletons. Everything real lands from Phase 2 onward |

### `frontend/` — configuration

| File | Role |
|---|---|
| `package.json` | React 19, TanStack Query, i18next, Zustand, react-router 7, Tailwind v4, date-fns. Scripts: `dev`, `build`, `typecheck`, `lint`, `test`, `api:types` |
| `vite.config.ts` | Dev server, the proxy from §1, manual chunk splitting, and a 300 kB chunk warning limit |
| `tsconfig.json` / `.app.json` / `.node.json` | Split so Node globals cannot leak into browser code. Strict, plus `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` |
| `eslint.config.js` | Includes the rule banning physical CSS utilities — see below |
| `.prettierrc.json` | Formatting |
| `Dockerfile` | Stages `deps` → `dev` / `build` / `dist` |
| `index.html` | Vite entry point |

**The ESLint RTL rule is the one to know about.** Hebrew is the primary language, so physical
CSS properties silently break the layout. The rule rejects `pl-`, `ml-`, `text-left`,
`border-l`, `rounded-r`, `left-`, `float-left` and friends inside `className` strings, template
literals, and `clsx`/`cn` calls. Use the logical equivalents instead:

```tsx
// ✅  ps-4  pe-2  ms-auto  me-1  start-0  end-0  text-start  text-end  border-s
// ❌  pl-4  pr-2  ml-auto  mr-1  left-0   right-0  text-left  text-right  border-l
```

Numbers, dates, versions, and identifiers inside Hebrew text need `dir="ltr"` or the
`ltr-embed` class — a bare `12/07` reads as `07/12` to a Hebrew reader, which is a data
integrity problem, not a cosmetic one.

### `frontend/src/`

| File | Role |
|---|---|
| `main.tsx` | Bootstrap. Awaits `initI18n()` **before** the first render, so the page never paints left-to-right and then flips |
| `App.tsx` | Providers: the TanStack Query client and Radix's `DirectionProvider` (Radix primitives read direction from context, not the DOM) |
| `i18n.ts` | i18next with `common` and `errors` namespaces, `load: 'languageOnly'` so `he-IL` resolves to `he` |
| `lib/rtl.ts` | Direction resolution, regional-tag handling, `applyDocumentDirection`, and `physicalToLogical` / `inlineSign` for the few places a physical answer is unavoidable — drag deltas, scroll offsets, arrow keys |
| `lib/cn.ts` | `clsx` + `tailwind-merge` class helper |
| `api/client.ts` | Typed fetch wrapper. Parses problem+json into an `ApiError` carrying a stable `code` for branching and a `translationKey` for display. Supports `If-Match` and `Idempotency-Key`. Has a marked seam where Phase 2 auth attaches |
| `hooks/useDirection.ts` | Current locale and direction |
| `hooks/useBreakpoint.ts` | Desktop versus mobile layout switching |
| `components/layout/AppShell.tsx` | Page frame |
| `components/common/LanguageToggle.tsx` | Hebrew / English switch |
| `features/system/SystemStatus.tsx` | The Phase 0 acceptance screen |
| `styles/index.css` | Tailwind v4 entry and `@theme` design tokens. There is **no** `tailwind.config.ts` — v4 is CSS-first |
| `locales/{he,en}/{common,errors}.json` | Translation strings |
| `test/setup.ts` | Vitest and jest-dom setup |
| `public/favicon.svg`, `public/manifest.webmanifest` | PWA manifest; the PNG icons it references arrive in Phase 8 |

`SystemStatus` is worth opening first. It is small, and it proves four things at once: the SPA
builds and mounts, i18n and RTL work, the Vite proxy reaches FastAPI, and FastAPI reaches
PostgreSQL and Redis. It also demonstrates two house rules — colour never conveys state on its
own (there is always a text label beside the dot), and `ms-auto` rather than `ml-auto`.

---

## 4. Testing

**67 tests: 52 backend, 15 frontend.** All passing as of the last recorded run.

### Backend unit tests — 25, no database

They use `httpx.ASGITransport`, which calls the application in-process without running the
lifespan, so no database or Redis connection is ever opened. Hermetic and fast.

- `tests/unit/test_config.py` — the production configuration guard accepts a valid config and
  rejects each unsafe one; CSV environment parsing is tested both as init keyword arguments
  **and** as real environment variables. That second path matters: the original test only
  passed values as kwargs, which does not exercise the env-var code path, and a startup crash
  slipped through.
- `tests/unit/test_health.py` — liveness always returns ok; readiness reports each dependency;
  the API root advertises locales; the request id is echoed when supplied and generated when
  absent; security headers are present; an unknown route returns problem+json.
- `tests/unit/test_logging.py` — native and stdlib loggers both emit; JSON output parses;
  request and user ids are attached; level filtering applies; exception info renders.

### Backend integration tests — 27, real PostgreSQL

[`tests/conftest.py`](../backend/tests/conftest.py) starts a throwaway `postgres:16-alpine`
container via **testcontainers** once per session, creates the four extensions, and runs
`alembic upgrade head` against it. Each test then runs inside a transaction that is rolled
back afterwards, so tests share a migrated schema but never share data.

Two choices in there are deliberate and load-bearing:

- **Migrations, not `metadata.create_all`.** This is what makes the suite prove the migrations
  are correct. `create_all` would happily build a schema the migrations cannot produce.
- **Never SQLite.** JSONB, GIN, `CITEXT`, generated columns, partial indexes, and `SKIP
  LOCKED` all behave differently or do not exist there. Testing against SQLite would validate
  the wrong database and pass while production breaks.

`tests/integration/test_schema_guarantees.py` asserts what the schema *promises*, not what it
looks like:

- `CITEXT` email case-folding, and a duplicate differing only in case being rejected
- Every `CHECK` constraint firing: `due_date >= start_date`, a task cannot be its own parent,
  priority range
- Cascades: deleting a task removes its subtasks; deleting a project removes its tasks and
  columns
- `ON DELETE RESTRICT`: deactivating a user preserves their task history
- Custom JSONB round-tripping every value shape, and containment queries hitting the GIN index
- Partial-index uniqueness: a column key is unique among live columns, and becomes reusable
  after a soft delete
- Generated search vectors matching Hebrew, and updating when a comment body changes
- The append-only audit guard, all four cases: `INSERT` succeeds, `UPDATE` is blocked,
  `DELETE` is blocked, and `DELETE` succeeds only after the maintenance opt-in
- An audit row surviving deletion of the entity it describes
- Seeded roles and permissions matching the registry, and the seed being idempotent
- Fractional-index insertion without rewriting neighbours, and precision surviving repeated
  halving
- One live invitation per email, a consumed invitation freeing the email, and the invitation
  token not being recoverable from the stored row
- Timestamps being timezone-aware UTC

`tests/factories.py` builds users, projects, tasks, columns, and invitations so tests do not
hand-roll fixtures and drift from the real shapes.

### Frontend tests — 15, Vitest in jsdom

- `src/lib/rtl.test.ts` (12) — Hebrew is RTL, English is LTR, regional tags reduce correctly,
  matching is case-insensitive, unknown locales fall back, `applyDocumentDirection` sets real
  `lang` and `dir` attributes, `physicalToLogical` inverts under RTL, `inlineSign` flips
  horizontal deltas.
- `src/components/common/LanguageToggle.test.tsx` (3) — renders both locales and marks the
  active one, **actually flips `<html dir>`** when switching, exposes an accessible group
  label.

### The gates run before finishing any change

```bash
cd backend  && uv run ruff check . && uv run mypy app && uv run lint-imports && uv run pytest
cd frontend && npm run typecheck && npm run lint && npm run test
```

Last recorded results: `ruff` clean, `mypy --strict` clean across 34 source files,
`import-linter` 4 contracts kept, `pytest` 52 passed, frontend 15 passed, `vite build`
producing roughly 108 kB gzipped against the 250 kB budget in `NFR-02`.

### The migration round-trip

The step that is usually skipped and later regretted:

```
alembic upgrade head    -> 28 tables (27 + alembic_version)
alembic downgrade base  ->  1 table, 0 orphaned functions or triggers
alembic upgrade head    -> 28 tables
```

Wired into CI, conditional on `alembic/versions/` being non-empty.

### CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs four jobs in parallel:

| Job | What it does |
|---|---|
| `backend` | Starts PostgreSQL and Redis service containers, creates the extensions, then runs ruff, ruff format, mypy strict, import-linter, pytest, and the migration round-trip |
| `frontend` | typecheck, lint, format check, tests, build; uploads `dist` as an artifact |
| `security` | gitleaks secret scan, `pip-audit`, `npm audit` |
| `docker` | Builds both images with layer caching, to catch Dockerfile breakage before deploy |

### What does not exist yet

Worth knowing so you do not go looking:

- No Playwright end-to-end tests. `frontend/e2e/` is Phase 8, though the auth flow gets its
  first e2e coverage in Phase 2.
- No `tests/api/` endpoint contract tests — there are no endpoints beyond health yet.
- No `tests/security/test_all_routes_declare_permission.py`. It arrives with the first real
  routers, and from then on it fails CI if any route omits its permission declaration.
- The coverage gate is configured (`fail_under = 80`) but **not enforced** in CI. It turns on
  in Phase 2, when `auth` and `permissions` are the modules being measured.
- The cross-module router-import contract is missing from `.importlinter`, because
  import-linter errors on modules that do not exist yet. Added in Phase 2.

---

## 5. Where the project stands

Phases 0 and 1 are complete and verified. Phase 2 (authentication) is next and is not blocked
on anything external. The authoritative, always-current status is the table at the top of
[`PROGRESS.md`](PROGRESS.md), followed by the "Next step" section at the bottom — read those
rather than trusting this paragraph.

In practical terms, what exists today is a running skeleton: containers healthy, migrations
round-tripping in both directions, a seeded Hebrew demo board in the database, and a React
shell that reaches through Vite to FastAPI to PostgreSQL and Redis and reports green.

What does not exist: any business endpoint, any login, any board UI. `app/modules/` is empty,
and so is most of `frontend/src/features/`.

Nine real defects were found and fixed across the two build sessions, every one caught by
running the code rather than reading it. `PROGRESS.md` records all nine with their root
causes; the two most instructive are `alembic/env.py` overwriting the test container's database
URL (so integration tests silently ran against an empty schema), and a PowerShell `-replace`
round-trip corrupting every Hebrew string in `seed.py`. The second one is now a standing rule:
never pipe source files through PowerShell string replacement — use the editor.

Seven items are waiting on external action (SendGrid domain authentication, Twilio Israeli
sender registration, and five decisions), listed under "Blocked / awaiting external action" in
`PROGRESS.md`. None of them block Phase 2.

---

## 6. Running it yourself

```bash
# 1. data layer
docker compose -f infra/docker-compose.yml up -d db redis

# 2. schema + demo data
cd backend
uv run alembic upgrade head
uv run python -m app.scripts.seed --reset

# 3. verify
uv run pytest                       # 52 passed

# 4. backend
uv run uvicorn app.main:app --reload        # http://localhost:8000/docs

# 5. frontend, in a second terminal
cd frontend && npm run dev                  # http://localhost:5173
```

Demo accounts — password `KavimDemo2026!` for all of them. They are seeded and visible in the
database now; they become usable once Phase 2 ships login.

| Email | Role |
|---|---|
| `admin@kavim.local` | System admin |
| `manager@kavim.local` | Line manager |
| `supervisor@kavim.local` | Shift supervisor |
| `worker1@kavim.local` … `worker3@kavim.local` | Worker |
| `auditor@kavim.local` | Viewer / auditor |

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `/health/ready` returns 503 with `"database":"unreachable"` | The `db` container is not up, or `.env` points somewhere else. `docker compose -f infra/docker-compose.yml ps` |
| `relation "users" does not exist` | Migrations have not been applied. `uv run alembic upgrade head` |
| Vite loads but every API call 404s | The backend is not running on `:8000`, or you opened `:8000` directly instead of `:5173` |
| ESLint fails on a class name you think is fine | It is almost certainly a physical CSS property. Swap to the logical equivalent (§3) |
| `MissingGreenlet` at runtime | An implicit lazy load. Add an explicit `selectinload` for the relationship |
| Want a completely clean database | `docker compose -f infra/docker-compose.yml down -v`, then repeat steps 1–2. This destroys all local data |

---

## 7. Before you finish any change

```bash
cd backend  && uv run ruff check . && uv run mypy app && uv run lint-imports && uv run pytest
cd frontend && npm run typecheck && npm run lint && npm run test
```

Then update [`PROGRESS.md`](PROGRESS.md): what changed, what is left, what blocks the next
step. That is the only reason the next session starts fast instead of re-deriving everything.
