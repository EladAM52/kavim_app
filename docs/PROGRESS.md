# Kavim — Build Log

Running record of what has been built, what was decided, what broke, and what must be done before the next step.

**How this document works.** One section per phase, appended as work happens. Each entry states what was delivered, what was verified (with evidence, not assertion), what went wrong, and what is outstanding. The **Next step** section at the bottom is always current — read that first.

| | |
|---|---|
| Spec | [`SPEC.md`](SPEC.md) |
| Structure | [`../PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) |
| Conventions | [`../CLAUDE.md`](../CLAUDE.md) |
| Onboarding | [`ONBOARDING.md`](ONBOARDING.md) — concept primers, file map, testing map |
| Last updated | 2026-07-27 |

---

## Status at a glance

| Phase | Scope | State |
|---|---|---|
| **0** | Foundation: repo, Docker, `core`, health endpoints, React shell with RTL/i18n, CI | ✅ **Complete and verified** |
| **1** | Data model, Alembic migration, seed script, integration test harness | ✅ **Complete and verified** |
| 2 | Auth: invite → OTP → register → login → refresh | ✅ **Complete and verified end to end.** Playwright coverage still owed |
| 3 | RBAC + admin panel | ⬜ |
| 4 | Projects, groups, column engine | ⬜ |
| 5 | Tasks, subtasks, cell editing, drag-drop | ⬜ |
| 6 | Comments, attachments, WebSocket live updates | ⬜ |
| 7 | Notifications: Gmail SMTP + preferences + quota accounting. SMS deferred | ⬜ |
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
| `config.py` | Typed `pydantic-settings`. A production-grade guard refuses to start if `SECRET_KEY` is the placeholder or under 32 chars, `APP_DEBUG` or `DATABASE_ECHO` is on, `APP_BASE_URL` is plaintext HTTP, SendGrid is enabled while still in sandbox, or storage is `local`. *(The SendGrid clause was replaced by the email guards in session 4.)* |
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

## Session 2 — 2026-07-26 · Phase 1: Data model

### Delivered

**27 tables** across 13 model modules, one initial Alembic migration (1789 lines), a realistic seed, and an integration test harness running against real PostgreSQL.

| Area | Tables |
|---|---|
| Sites | `sites`, `lines` |
| Identity | `users`, `notification_preferences` |
| Authorization | `roles`, `permissions`, `role_permissions`, `user_roles` |
| Auth artefacts | `invitations`, `otp_codes`, `refresh_tokens`, `password_reset_tokens` |
| Projects | `projects`, `project_members`, `groups`, `saved_views`, `board_columns` |
| Tasks | `tasks`, `task_assignees`, `task_cell_history`, `task_dependencies` |
| Collaboration | `comments`, `attachments` |
| Notifications | `notification_outbox`, `notification_deliveries`, `in_app_notifications` |
| Audit | `audit_log` |

New `core` modules (all bottom-of-graph, no dependency on `models`):

| Module | Purpose |
|---|---|
| `core/enums.py` | All shared enumerations. **Moved out of `models/`** because `core.permissions` needs `RoleKey` and the layering contract forbids `core → models` |
| `core/permissions.py` | 30 permission strings + the seeded role matrix + `resolve_effective_permissions` (layers 1 ∩ 2). Data only; the `require_permission` dependency lands in Phase 3 |
| `core/security.py` | argon2id password hashing with transparent rehash, SHA-256 token/OTP digests, `secrets`-based generation, constant-time compare, and `waste_password_time()` for the no-enumeration login branch |
| `core/time.py` | `utc_now`, `local_today`, `start_of_local_day`, `is_within_quiet_hours` (handles a window that wraps midnight) |

### Design decisions made during implementation

| Decision | Reasoning |
|---|---|
| **Enums live in `core`, not `models`** | `core.permissions` needs `RoleKey`. The layering contract puts `models` above `core`, so the import had to go the other way |
| **`VARCHAR` + `CHECK` instead of native PostgreSQL `ENUM`** | Adding a value to a native enum needs `ALTER TYPE`, which complicates expand/contract deploys. A `CHECK` gives the same integrity for a plain `ALTER` |
| **Constraint naming convention on `MetaData`** | Without it PostgreSQL invents names, Alembic cannot match them, and `downgrade` fails on names it never knew — exactly when you need it to work |
| **`search_vector` as a generated column** | Maintained by PostgreSQL, so it cannot drift from the source text. No trigger, no application code to forget |
| **`lazy="raise_on_sql"` on every relationship** | Under async SQLAlchemy an implicit lazy load raises `MissingGreenlet` at runtime. Making it raise at development time forces explicit `selectinload` |
| **`viewonly=True` on `User.roles` / `Role.users`** | `user_roles` carries `assigned_by` and `assigned_at`; a plain association write would silently drop them. Assignment goes through `UserRole` rows |
| **Seed script at `app/scripts/seed.py`, not `infra/scripts/`** | It imports the application, so it must live on the package path. Runs as `python -m app.scripts.seed`, which also works unchanged inside the container. **Deviates from `PROJECT_STRUCTURE.md`, now updated** |
| **`audit_log` append-only via trigger, not `GRANT`** | The app connects as the table owner in development, and an owner always retains full privileges — so a grant alone would not hold. A trigger holds for every role, including a buggy ORM call, which is the real threat model |
| **Audit `DELETE` has a deliberate opt-in** | Retention must still prune rows past 24 months, so the maintenance task sets `SET LOCAL kavim.audit_maintenance = 'on'`. Application code cannot delete; the scheduled job can |
| **`entity_id` on `audit_log` is not a foreign key** | The record of a deletion has to outlive the deleted row. A cascade would erase the history of exactly the deletions most worth auditing |

### Problems found and fixed

Five real defects, every one caught by running the code rather than reading it.

1. **`core` importing `models` broke the layering contract.** Caught by `import-linter`, not by review. Fixed by moving `enums.py` into `core`.
2. **`AmbiguousForeignKeysError` on `Role.users`.** `user_roles` has two FKs to `users` (`user_id`, `assigned_by`), so the secondary join was ambiguous. Fixed with explicit `primaryjoin`/`secondaryjoin`.
3. **The seed violated its own `due_date >= start_date` constraint.** Due dates were generated independently of start dates, so an "overdue" task got a start date after its due date. The constraint did its job; the generator now derives `start` from `due`. A test now pins this.
4. **`alembic/env.py` overwrote the caller's database URL.** It unconditionally set `sqlalchemy.url` from settings, so the integration-test fixture's container URL was clobbered and migrations ran against the *development* database — leaving tests to run on an empty schema (`relation "users" does not exist`). Now honours a URL already set.
5. **The seed crashed on exit.** `main()` called `dispose_engine()` inside a second `asyncio.run()`, and asyncpg connections belong to the loop that created them (`'NoneType' object has no attribute 'send'`). Disposal moved inside the same loop.

Also: `date.today()` reads the *process* timezone, so on a UTC server at 01:00 Jerusalem time it returns yesterday — an overdue scan would fire a day early. Ruff's `DTZ` rules flagged all three uses; `core/time.py` now provides `local_today()`.

**Self-inflicted, worth recording:** a PowerShell `-replace` round-trip over `seed.py` corrupted every Hebrew string (PS 5.1 read UTF-8 bytes as Windows-1252 and wrote them back as UTF-8) and prepended a BOM. Repaired with a byte-wise reverse mapping and verified against the database. Lesson: never pipe source files through PowerShell string replacement — use the editor.

### Verification evidence

```
ruff           All checks passed!
ruff format    42 files already formatted
mypy --strict  Success: no issues found in 34 source files
import-linter  Contracts: 4 kept, 0 broken
pytest         52 passed          (25 unit + 27 integration)
```

Migration round-trip, the step that is usually skipped and later regretted:

```
alembic upgrade head    -> 28 tables (27 + alembic_version)
alembic downgrade base  ->  1 table, 0 orphaned functions or triggers
alembic upgrade head    -> 28 tables
```

The append-only guard, all four cases:

```
INSERT                                        -> ok
UPDATE audit_log                              -> ERROR: append-only: UPDATE is never permitted
DELETE audit_log                              -> ERROR: append-only: DELETE requires SET LOCAL …
SET LOCAL kavim.audit_maintenance='on'; DELETE -> DELETE 1
```

Seed output, idempotent on re-run:

```
permissions 30 | roles 5 | role_permissions 83 | users 7
projects 1 | board_columns 12 | groups 3
tasks 43 (20 parents + 23 subtasks) | task_assignees 43 | comments 12
```

Hebrew renders correctly from the database after the encoding repair:

```
קו 3 — ביקורת היגיינה שבועית
סטטוס | אחראי | תאריך התחלה | תאריך יעד
בדיקת ניקיון מסוע ראשי
```

The 27 integration tests assert the schema's actual promises, not its shape: CITEXT case-folding, every CHECK constraint firing, cascade behaviour, `ON DELETE RESTRICT` protecting a user with history, JSONB type round-tripping, GIN containment queries, partial-index uniqueness letting a soft-deleted column key be reused, generated search vectors matching Hebrew, fractional-index insertion without neighbour rewrites, and one-live-invitation-per-email.

### Demo accounts

Password for all: `KavimDemo2026!`

| Email | Role |
|---|---|
| `admin@kavim.example.com` | System admin |
| `manager@kavim.example.com` | Line manager |
| `supervisor@kavim.example.com` | Shift supervisor |
| `worker1@kavim.example.com` … `worker3@kavim.example.com` | Worker |
| `auditor@kavim.example.com` | Viewer / auditor |

The demo board demonstrates the column-permission asymmetry that FR-205 exists for: workers can edit `Status`, `Due date`, `Station`, `Measured temperature`, and `Corrective action`, but `Verified` and `Root cause approved by` are manager-only — a worker cannot sign off their own fix.

### Known gaps left deliberately

| Gap | Why, and when it closes |
|---|---|
| Cross-module router contract still absent from `.importlinter` | Added in Phase 2 with the first routers |
| Coverage gate still not enforced | Turns on in Phase 2, when `auth` and `permissions` are the modules being measured |
| `core/permissions.py` has no `require_permission` dependency yet | Phase 3, with the admin panel |
| Restricted database role for production | The trigger covers the threat; the role split is an operations task for Phase 17 |
| Nothing committed since `45224b9` | Phase 1 work is uncommitted and unpushed |

---

## Session 3 — 2026-07-27 · Orientation + CSP fix

### Delivered

- `docs/ONBOARDING.md` — teaching document for someone new to Docker, FastAPI, or React +
  Vite. Concept primers, the dev and production runtime topologies, a file-by-file map, the
  testing map, a run-it-yourself sequence, and a troubleshooting table. Links to `SPEC.md`,
  `PROJECT_STRUCTURE.md`, and this file rather than restating them, so there is still one
  build log and one contract.

### Problem found and fixed

**`/docs` rendered an empty page.** Swagger UI loads its bundle from `cdn.jsdelivr.net` and
its favicon from `fastapi.tiangolo.com`, but the development CSP allowed `script-src 'self'`
only — so the HTML shell arrived, the title rendered, and the browser refused both assets.
`/openapi.json` was always fine, which is why nothing else looked broken.

Fixed in `core/middleware.py` by branching the CSP on `_DOCS_PATHS` and allowing the CDN
**only** there. The SPA's CSP is unchanged and must never depend on a third-party origin.
Production is unaffected either way: `docs_url` and `redoc_url` are `None` there.

Two tests pin both halves — that `/docs` carries the allowance, and that `/health/live` does
not.

### Verification evidence

```
ruff           All checks passed!
ruff format    42 files already formatted
mypy --strict  Success: no issues found in 34 source files
import-linter  Contracts: 4 kept, 0 broken
pytest         54 passed          (27 unit + 27 integration)
```

Live, after restarting uvicorn:

```
GET /docs         Content-Security-Policy: … script-src … https://cdn.jsdelivr.net …
GET /health/live  Content-Security-Policy: … script-src 'self' 'unsafe-inline' 'unsafe-eval'
GET /health/ready {"status":"ready", … database:"ok", redis:"ok"}
```

**Worth remembering:** `uvicorn --reload` applied only the first of two edits to the same file
and then went quiet — the second edit never took effect and the served header stayed stale for
several minutes. When a change provably passes its test but the running server disagrees,
restart before debugging the code.

---

## Session 4 — 2026-07-27 · Email moves to Gmail SMTP; SMS deferred

Architecture change, decided by the user. Recorded as **ADR-007** in `SPEC.md` §15.

### The decision

| | Before | After |
|---|---|---|
| Email | SendGrid API, hosted dynamic templates, delivery webhooks | Authenticated SMTP to `smtp.gmail.com:587` as `kavimsupport@gmail.com`, Jinja2 templates in git, no webhooks |
| SMS | Twilio, Israeli sender registration pending | **Deferred.** No provider. Schema and enums unchanged |

**Why:** the pilot is one line and under 50 workers. SendGrid's advantages — a raised ceiling,
delivery webhooks, hosted templates — are all things the pilot does not need, and domain
authentication (SPF + DKIM) needed IT and was sitting on the critical path for testing the auth
flow. An App Password takes two minutes. Twilio was worse: regulated multi-day sender
registration for a channel with no identified requirement yet.

### What this costs, recorded so nobody rediscovers it later

- **~500 recipients/day**, hard. Exceeding it suspends sending for 24 hours, which would take
  OTP and invitation mail down with it. Answered by FR-714 quota accounting with
  prioritization, not by hoping.
- **No delivery or bounce webhooks.** `notification_deliveries.status` now means "the relay
  accepted it", not "the inbox received it" (FR-713). Bounces arrive as mail to
  `kavimsupport@gmail.com` and are not ingested.
- **`From` is locked to the Gmail account.** Free Gmail rewrites anything else *silently*
  rather than rejecting it, so `no-reply@kavim.example.com` is unavailable and mail visibly comes
  from `@gmail.com` — which corporate spam filters treat with suspicion (R13). Test against
  the plant's real mail domain early, not at pilot.

One thing got *better*: templates move from hosted SendGrid dynamic templates into
`modules/notifications/templates/`, so copy changes are reviewed in a pull request, they diff,
and they cannot drift between environments.

### Delivered

| Area | Change |
|---|---|
| `SPEC.md` | FR-701 rewritten; FR-702/711/112 marked `WON'T (this release)`; FR-706 quiet hours now cover email; **FR-713** (delivery-reporting limits) and **FR-714** (quota) added; NFR-21 recosted; architecture diagram, notification pipeline, and auth failure modes updated; §6.14 rewritten around the `EmailSender` seam with new §6.14.1 recording exactly what SMS keeps; webhook routes and phone-verification routes marked deferred; §12.1 env table replaced; roadmap Phases 2 and 7 restated; risks R2/R3 replaced and R13/R14 added; **ADR-007** written |
| `core/config.py` | 13 `SENDGRID_*` and 5 `TWILIO_*` settings replaced by 13 `EMAIL_*`/`SMTP_*` ones. New `_guard_email` validator, plus `email_from_address` and `is_gmail_smtp` derived properties |
| `.env.example` | New email block with step-by-step App Password instructions; SMS block reduced to a pointer at §6.14.1 |
| `pyproject.toml` | `sendgrid` and `twilio` out; `aiosmtplib` and `jinja2` in; `aiosmtpd` added as a dev dependency for the in-process SMTP sink |
| `.importlinter` | `sdk-containment` now forbids `aiosmtplib`, `smtplib`, `boto3` outside `integrations/` |
| `CLAUDE.md`, `PROJECT_STRUCTURE.md`, both `README.md`s, `ONBOARDING.md` | Provider references updated; `integrations/` tree now shows `email.py` + `smtp_client.py`; `notifications/quota.py` added to the planned tree |

### Design decisions made during implementation

| Decision | Reasoning |
|---|---|
| **Two files, not one: `integrations/email.py` + `smtp_client.py`** | `modules/` depends on the `EmailSender` protocol and has no idea SMTP exists. Swapping providers is the only route back to real delivery tracking, so the seam is load-bearing rather than speculative |
| **`smtplib` is forbidden outside `integrations/`, not just `aiosmtplib`** | Being in the standard library makes it easier to reach for absent-mindedly, not more acceptable |
| **`_guard_email` runs in every environment, not only production** | A broken transport fails at first send. For OTP that is the moment a user is already locked out and waiting — far too late to discover a missing App Password |
| **Gmail `From`/username mismatch is a hard error** | Gmail does not reject an unauthorized sender, it substitutes its own. A silent wrong-sender is worse than a startup failure, and this is the only place it can be caught |
| **`535` is non-retryable** | A revoked App Password will not fix itself. Five backed-off attempts turn a visible outage into a quiet one |
| **SMS enums and columns stay** | `NotificationChannel.SMS` generates a `CHECK` constraint. Removing it means a migration now and another one later. Keeping it means adding SMS costs no schema change at all |

### Verification evidence

```
uv sync        - sendgrid, - twilio, + aiosmtplib, + aiosmtpd, + jinja2
ruff           All checks passed!
ruff format    42 files already formatted
mypy --strict  Success: no issues found in 34 source files
import-linter  Contracts: 4 kept, 0 broken
pytest         61 passed          (was 54; +7 email config tests)
```

The seven new tests cover both guards: STARTTLS and implicit TLS are mutually exclusive,
sending without a password is rejected, a Gmail `From` mismatch is rejected, the `From` falls
back to `SMTP_USERNAME`, dry-run needs no credentials, and production rejects
`EMAIL_DRY_RUN=true`, `EMAIL_ENABLED=false`, and plaintext submission.

### Not built yet — deliberately

The integration files themselves (`email.py`, `smtp_client.py`, the templates, `quota.py`) are
**Phase 2 work**, listed in the "Next step" section. This session changed the contract, the
configuration, the dependencies, and the boundary rules — the decisions that are expensive to
reverse once code is written against them. Writing the client before the contract settled is
the sequence that produces rework.

---

## Session 5 — 2026-07-27 · Phase 2 backend: authentication

### Delivered

10 auth endpoints, the email transport, and 38 new tests. **99 passing, 84% coverage.**

| Area | Files |
|---|---|
| JWT + refresh helpers | `core/security.py` — access tokens, the 15-minute `scope=register` ticket, refresh families. Scope is checked on decode, so a registration ticket cannot authenticate API calls |
| Rate limiting | `core/rate_limit.py` — Redis sliding window via a Lua script, so check-and-consume is atomic. Fails **open**, justified below |
| Contract | `schemas/auth.py` — the OpenAPI source the frontend types generate from |
| Email transport | `integrations/email.py` (the `EmailSender` protocol) + `integrations/smtp_client.py` (aiosmtplib, dry-run, SMTP error classification) |
| Templates | `modules/notifications/templates/` — `invitation`, `otp_code`, `password_reset`, `account_locked`, each `he` + `en`, rendered with Jinja2 |
| Outbox write path | `modules/notifications/service.py` — queues rows in the caller's transaction, sends nothing |
| Audit write path | `modules/audit/service.py` — 16 named auth actions, secret scrubbing |
| Auth module | `modules/auth/` — `invitations.py`, `otp.py`, `passwords.py`, `service.py`, `dependencies.py`, `router.py` |

Endpoints: `GET /auth/invitations/{token}`, `POST /auth/otp/request`, `/otp/verify`, `/register`,
`/login`, `/refresh`, `/logout`, `/logout-all`, `/password-reset/request`, `/password-reset/confirm`.

### Three real bugs the tests caught, all the same shape

Worth recording as a class, because it will recur in every module that records a
failure:

**`get_db` rolls back on exception, and security bookkeeping describes the failure
being raised.** So the write was undone by the very error that recorded it:

| Where | Consequence if shipped |
|---|---|
| `login` — `failed_login_count` increment | The counter never reaches 10. **Account lockout could never fire.** |
| `otp.verify_otp` — `attempts` increment | The attempt counter stays at 0. **A 6-digit code was guessable without limit.** |
| `rotate_refresh_token` — family revocation | Reuse detection detects theft and then **revokes nothing.** |

Each now commits before raising, commented at the site. This was only findable
because the test fixture mirrors the real dependency's rollback — a fixture that
just yielded the session would have shown all three as passing.

Two more, found the same way:

- **`Content-Language` was never sent.** `EmailMessage.set_content()` calls
  `clear_content()`, which deletes every `Content-*` header — so setting it before
  the body silently dropped it. Now set after.
- **Coverage was lying.** Async SQLAlchemy runs database IO inside greenlets and
  coverage loses the trace there: `service.py` measured 38% while every one of its
  tests was green. `concurrency = ["greenlet", "thread"]` fixed it — the same file
  now reads 92%. A misleading coverage number is worse than none.

### Design decisions made during implementation

| Decision | Reasoning |
|---|---|
| **Rate limiting fails open** | Every limit sits in front of a database counter: `failed_login_count`, `otp_codes.attempts`, single-use tokens. Redis is the cheap first line; the database holds the guarantee. Failing closed would turn a Redis restart into a total login outage in exchange for protection that is already there |
| **Refresh tokens are opaque, not JWTs** | A refresh token must be revocable. A self-contained JWT cannot be revoked before it expires, which defeats reuse detection entirely |
| **Access tokens re-load the user every request** | A JWT stays valid until expiry, so a user deactivated 30 seconds ago would still authenticate from the token alone. The 15-minute lifetime bounds the window; the database read closes it |
| **`EmailStr` inbound, plain `str` outbound** | Validating an inbound address catches a typo. Validating an outbound one re-checks what the database already holds, so it can only turn a readable row into a 500 — which is exactly what happened with an address at a special-use domain |
| **Demo and test addresses moved to `example.com`** | `.local` and `.test` are special-use TLDs that RFC-compliant validation rejects, so `admin@kavim.example.com` **could not have logged in**. `example.com` is IANA's documentation domain: valid syntax, guaranteed undeliverable |
| **`404` for an unknown invitation token, `410` for a spent one** | Both require possessing a 256-bit token, so neither is an oracle — and "your link expired" is materially more useful than "not found" |
| **The router contract is now enforced** | Deferred since Phase 0 because import-linter errors on absent modules. Verified by probe: a temporary cross-module router import reported `BROKEN`, and removing it reported `KEPT` |

### Verification evidence

```
ruff           All checks passed!
mypy --strict  Success: no issues found in 50 source files
import-linter  Contracts: 5 kept, 0 broken
pytest         99 passed          (was 61)
coverage       84%  — modules/auth: service 92%, router 99%, otp 95%, passwords 94%
```

Live against the running server, after re-seeding:

```
POST /auth/login  wrong password           -> 401  (identical body for an unknown address)
POST /auth/login  admin@kavim.example.com  -> 200  SYSTEM_ADMIN, 30 permissions
set-cookie: kavim_refresh=…; HttpOnly; Max-Age=2592000; Path=/api/v1/auth; SameSite=strict
```

Hebrew mail renders and dry-run sends:

```
SUBJECT      קוד האימות שלך: 482913
SUBJ HDR     =?utf-8?…  (RFC 2047 encoded)
CONTENT-LANG he
MULTIPART    True   (text + html alternative)
SENT         dry_run=True accepted=1
```

### Known gaps — read before continuing

| Gap | Consequence |
|---|---|
| **The outbox sweeper does not exist** | Invitation and OTP mail is *queued and never delivered*. The flow is complete server-side and the tests read the code out of the outbox payload, but **a human cannot currently complete registration.** This is the single blocking item for a usable Phase 2 |
| **No frontend** | `features/auth/` is not built. No router, no auth store, no login screen. `api/client.ts` still has an empty seam |
| **No admin endpoint to create an invitation** | Invitations can only be created in code or by the seed. `POST /admin/invitations` is Phase 3 |
| Login throttle and lockout are both 10 | They trip on the same attempt and the throttle answers first, so a locked-out user sees `429` for the rest of the window, then `403`. Both deny; only the message differs |
| `require_permission` not written | Phase 3. No Phase 2 endpoint needs it |
| Per-module 90% coverage not mechanically enforced | `coverage` has no per-module `fail_under`. The global gate is 80% and enforced; the auth files are measured at 92–99% and checked by reading the report |
| Seed needed a UTF-8 stdout fix on Windows | `python -m app.scripts.seed` died with `UnicodeEncodeError` on a cp1252 console *after* writing rows, which looked like a data bug. The script now reconfigures its streams |

### Next step

1. **Outbox sweeper** — `workers/tasks_notifications.py`: claim with `SKIP LOCKED`, render, send, write `notification_deliveries`, honour the retryable/permanent split (`535` dead-letters immediately). Plus the beat entry. Without this nothing is delivered.
2. **Frontend `features/auth/`** — router, auth store with the access token in memory only, `InvitationLanding`, `OtpVerify`, `Register`, `Login`, `ForgotPassword`, Hebrew RTL mobile-first.
3. **`api/client.ts`** — attach `Authorization`, refresh-on-401 with a single-flight guard so concurrent 401s trigger exactly one refresh.
4. **Playwright** — invite → OTP → register → login → refresh → logout in both locales.

---

## Session 6 — 2026-07-27 · The outbox sweeper

The gap that made Phase 2 unusable: mail was queued and never delivered. It is
delivered now, and **a human can complete registration end to end**.

### Delivered

| File | Role |
|---|---|
| `modules/notifications/outbox.py` | Claim with `SKIP LOCKED`, render, send, record. Backoff 1m/5m/25m/2h/10h, dead-letter after 5 |
| `modules/notifications/quota.py` | Rolling 24-hour recipient count against the Gmail ceiling (FR-714), with an urgent-only reserve |
| `workers/tasks_notifications.py` | The Celery boundary: `kavim.notifications.sweep_outbox` and a queue-depth probe |
| `workers/beat_schedule.py` | The sweep every 30s, `expires=25` so a missed tick is dropped rather than queued |
| Migration `09adde4def09` | `notification_deliveries.recipient_id` nullable, a CHECK that a row names a user *or* an address, and `deferred_quota` added to `delivery_status` |

20 new tests. **119 passing, 83% coverage**, 5/5 import contracts.

### Four real bugs, all found by running it

**1. The delivery log could not cover invitation or OTP mail.** `recipient_id` was
`NOT NULL`, but both precede the `users` row by design — so there was no record for
exactly the mail whose failure is most costly: a bad invitation address means the
user never registers, and nobody could see why. Now nullable, with `destination`
carrying the address and a CHECK that at least one is present.

**2. A two-millisecond clock skew stalled freshly queued mail.** `next_attempt_at`
defaults to PostgreSQL's `now()`, and `claim_batch` compared it against the *host*
clock. Measured skew between the container and the host was 2 ms — enough that a
just-queued OTP looked not-yet-due and waited for the next tick. It surfaced as
tests that passed alone and failed in sequence. Fixed by comparing with
`func.now()`, so both sides come from the database.

**3. Registration returned an empty roles list.** The sessionmaker sets
`autoflush=False`, so the `UserRole` insert was invisible to `build_identity`'s
SELECT in the same transaction. The database was correct; the *response* told the
client it had no roles and no permissions, which would have rendered a
permission-less shell until the next refresh. Fixed with an explicit flush — and
the test session now also sets `autoflush=False`, because with autoflush on this
class of bug passes under test and fails in production.

**4. A read-after-write race on every write endpoint.** Caught in a live run:

```
18:46:02  POST /auth/register  status=201   user_registered
18:46:02  POST /auth/login     status=401   "Email or password is incorrect."
```

The account existed with a valid hash moments later. `get_db` commits in the
teardown of a `yield` dependency, and **FastAPI runs that after the response has
been sent** — so a client acting immediately on a 201 can beat the commit. The auth
router now commits explicitly before responding, at nine points. The dependency's
commit becomes a no-op and its rollback-on-exception safety net still applies.

This one generalises: *every* write endpoint added from here needs the same
treatment, or a fast client can observe its own write missing. Worth turning into a
lint or a shared helper when the next module lands.

### Design decisions

| Decision | Reasoning |
|---|---|
| **`asyncio.run` per Celery task, engine disposed inside the same loop** | asyncpg connections belong to the loop that created them; a pooled connection carried into the next task's loop fails with `'NoneType' object has no attribute 'send'`. That exact mistake already cost a session in the seed script. A fresh connection per 30-second sweep is free |
| **No Celery autoretry on the sweep** | Rows it did not process are still pending and the next tick takes them. Retrying would re-send the ones it *did* process |
| **`dispatch_row` never raises** | The row's status is the error channel. One malformed message must not stall every message queued behind it — there is a test for exactly that |
| **A quota deferral does not spend a retry** | The refusal is ours, not the provider's. Charging an attempt for it would dead-letter mail that was never actually attempted |
| **`deferred_quota` is its own status** | Quiet-hours and quota deferral both mean "deliberately not sent yet", but they send an admin to different places — a user's schedule versus the Gmail ceiling |
| **`allow_indirect_imports` on the SDK contract** | Anything that actually sends mail reaches `aiosmtplib` transitively through the integrations seam. That is the seam working. Counting the indirect chain made the contract unsatisfiable for the sweeper, so it was scoped to direct imports and re-verified by probe |

### Verification evidence

```
ruff / format   All checks passed!
mypy --strict   Success: no issues found in 53 source files
import-linter   Contracts: 5 kept, 0 broken
pytest          119 passed   (stable across 3 consecutive runs)
coverage        83%
migration       upgrade -> downgrade -> upgrade -> downgrade -> upgrade, 28 tables
```

The full flow, live, three consecutive runs:

```
1 landing            200 sixthhire@example.com / עובד
2 otp requested      202
3 sweep              claimed=1 sent=1
4 delivery recorded  status=sent to=sixthhire@example.com recipient_id=None
5 otp verified       200
6 registered         201 name='עובד חדש' roles=['WORKER'] perms=7
7 login              200
8 invitation spent   410
```

Hebrew survives the round trip: the stored name is 13 characters in 24 bytes of
UTF-8. (An earlier `curl` run showed `????` — that was the Windows console mangling
the request body before it left, not the application. Verified by re-sending through
a UTF-8 client and comparing stored bytes.)

### Known gaps

| Gap | Consequence |
|---|---|
| **No frontend** | Still the blocking item for a usable Phase 2. `features/auth/` is not built; `api/client.ts` has an empty seam |
| No admin endpoint to create invitations | Only creatable in code. `POST /admin/invitations` is Phase 3 |
| The Celery worker and beat are not running locally | The sweep was exercised by calling the task directly. Start them with `docker compose --profile app up worker beat`, or run the task by hand as above |
| `workers/` is at 0% coverage | The Celery boundary is thin and untested; the logic it calls is covered at 90%+ |
| Quiet hours not applied | The sweeper does not yet consult `quiet_hours_start/end`. Phase 7, with recipient resolution |

### Next step

Frontend `features/auth/`: router, auth store with the access token in memory only,
`InvitationLanding`, `OtpVerify`, `Register`, `Login`, `ForgotPassword` — Hebrew RTL,
mobile-first. Then `api/client.ts`: attach `Authorization`, refresh-on-401 with a
single-flight guard. Then Playwright across both locales.

---

## Session 7 — 2026-07-27 · Phase 2 frontend: the auth screens

Phase 2 is now usable end to end from a browser. **33 frontend tests** (was 15),
119 backend, all gates green.

### Delivered

| Area | Files |
|---|---|
| Generated types | `api/generated/types.ts` — 829 lines from the live OpenAPI schema. A backend field rename is now a frontend compile error |
| Session store | `stores/auth.ts` — access token in a module closure, never in any browser storage |
| Client seam | `api/client.ts` — bearer header, refresh-on-401, single-flight guard, one replay |
| UI primitives | `components/ui/` — `Button`, `Field`, `Alert`. Logical CSS only, 44px targets |
| Screens | `features/auth/` — `InvitationLanding`, `OtpVerify`, `Register`, `Login`, `ForgotPassword`, `ResetPassword`, plus `AuthLayout`, `RequireAuth`, `useAuthError` |
| Routing | `router.tsx` — lazy auth chunks, public/private split, boot refresh gate |
| Strings | `locales/{he,en}/auth.json` — full namespace, no hardcoded copy |

### The decisions worth knowing

**The single-flight refresh is not an optimisation.** A board screen fires several
queries at once; when the access token expires they all 401 together. Six parallel
refreshes would each rotate the token, five of them presenting a value the others
just spent — and the backend correctly reads that as replay and revokes the whole
family (SPEC §8.2). The user gets signed out for loading a page. Verified by probe:
with the `??=` guard removed the test reports **6 refreshes instead of 1**, then
`KEPT` at 1 when restored.

**Auth entry paths never trigger a refresh.** A wrong password would otherwise
provoke a refresh attempt and a login retry — turning one user mistake into two
attempts against a limit of ten before lockout.

**`RequireAuth` distinguishes `unknown` from `anonymous`.** The token is memory-only,
so a reload starts with nothing and the httpOnly cookie is the only evidence a
session exists. Treating "not asked yet" as "signed out" would bounce every user to
the login screen on every refresh.

**The registration screen has no email field at all.** Not disabled — absent. The
API takes the address from the invitation and rejects a submitted one with a 422, so
rendering an input would offer a choice that does not exist.

**Emails, passwords, phone numbers, and the OTP get `dir="ltr"`.** An LTR sequence
rendered RTL reads back in the wrong order; for a verification code that means the
user types what they see and it is wrong. There is a test asserting the attribute.

**The forgot-password screen shows the same success copy regardless.** "If that
address is registered…" matches the backend's identical 202, because saying "sent!"
only for real addresses would rebuild in the UI the enumeration oracle the API
carefully avoids.

### Verification evidence

```
tsc -b            no errors
eslint .          clean  (incl. the physical-CSS ban)
prettier --check  All matched files use Prettier code style!
vitest run        33 passed   (12 rtl, 6 store, 8 client, 3 toggle, 4 login)
vite build        built in 1.35s
```

Bundle, gzipped: `index` 73.6 kB + `react` 32.1 kB + `i18n` 17.6 kB + `query`
15.6 kB + CSS 4.9 kB ≈ **144 kB** against the 250 kB budget in NFR-02. Each auth
screen is its own chunk under 1 kB gzipped, so a signed-in worker never downloads
them.

The whole flow driven through the **Vite proxy** — the same path the SPA takes:

```
   emailed link: http://localhost:5173/invite/XX10hDzeSqln…
1  GET  invitation       200  browsertest@example.com
2  POST otp/request      202
3  sweep                 sent=1  code=989963
4  POST otp/verify       200
5  POST register         201  roles=['WORKER']
6  cookies held          ['kavim_refresh']
   refresh cookie        path=/api/v1/auth  httponly=True
7  POST refresh          200  new_token=True
8  POST refresh replay   401  (reuse detected, family revoked)
```

### Known gaps

| Gap | Consequence |
|---|---|
| **No visual browser check** | Verified by component tests and by driving the API through the proxy, *not* by looking at rendered pages. RTL layout is the project's central risk and layout is exactly what these tests do not assert — worth ten minutes with a browser at both locales |
| **No Playwright** | The `e2e/` specs in `PROJECT_STRUCTURE.md` are still unwritten. This is the coverage that would catch an RTL layout regression |
| No `/users/me` endpoint | The boot refresh returns the identity, so nothing needs it yet. It arrives in Phase 3 |
| Only one authenticated route | `/` renders `SystemStatus`. The board arrives in Phase 5 |
| Sign-out is not in the UI | `authApi.logout` exists and is tested at the client level; no button calls it yet, because the app shell gets its user menu in Phase 3 |
| The breakpoint chip still ships | The `xs`/`sm`/`md` chip in the header is a development affordance and should be gated behind `import.meta.env.DEV` |

### Next step

Phase 3 — RBAC and the admin panel: `require_permission` as a FastAPI dependency,
`POST /admin/invitations` (so invitations stop needing a script), the role × permission
matrix editor, the audit log view, and `tests/security/test_all_routes_declare_permission.py`
to make CLAUDE.md rule 2 mechanical.

Worth doing first, or alongside: Playwright over the auth flow in both locales, while
the flow is fresh.

---

## Blocked / awaiting external action

These have lead times and are **not** blocked on development. Starting them now keeps them off the critical path.

| # | Item | Blocks | Owner | Notes |
|---|---|---|---|---|
| E1 | **Gmail App Password** for `kavimsupport@gmail.com` | Real sending in Phase 2; nothing before that | User | ~2 minutes: enable 2-step verification, then <https://myaccount.google.com/apppasswords>. Development is unblocked meanwhile because `EMAIL_DRY_RUN=true` renders and logs without connecting. **Paste it into `.env`, never into a committed file** |
| E1b | **Deliverability check against the plant's real mail domain** | Phase 2 acceptance | User | Send one invitation to a real work address early and confirm it does not land in spam. A `@gmail.com` sender to a corporate domain is the risk (SPEC R13). Finding this at pilot instead of now is the expensive version |
| ~~E2~~ | ~~Twilio account + Israeli sender registration~~ | — | — | **Dropped.** SMS deferred, no provider integrated (SPEC §6.14.1, ADR-007) |
| E3 | Decision: is **Entra ID SSO** expected within 12 months? | Phase 2 design | User | Changes how much to invest in the password flow. The model keeps `auth_provider` / `external_idp_id` either way |
| E4 | Decision: required **retention period for quality records** | Audit and soft-delete design | User + QA/compliance | Currently assumed 24 months |
| E5 | **Existing quality checklists or forms** (photo or Excel of a real one) | Phase 4 default column set and templates | User | Highest-value input available. Turns generic templates into ones that are immediately useful |
| E6 | **Pilot cohort**: which line, which shift, how many workers | Phase 8 scope | User | |
| E7 | **Plant Wi-Fi coverage measurement** at the stations workers use | Phase 8 offline scope | User | Determines whether the offline queue needs to be more or less capable than currently planned |

---

## Next step — Phase 2: Authentication

Nothing external blocks this. Email defaults to `EMAIL_DRY_RUN=true`, which renders each
message and logs it without opening a connection, so the whole flow is testable before the
App Password exists (E1).

**To do**

1. ~~Commit and push Phase 0 + Phase 1~~ — done, `fb83fd9`.
2. `core/security.py` additions: JWT encode/decode, the short-lived `registration_ticket`, refresh-token rotation helpers.
3. `core/rate_limit.py` — Redis token bucket: login 10 per 15 min per IP *and* per email, OTP verify 5 per code, OTP request 3 per 15 min per email.
4. `schemas/auth.py` — Pydantic request/response models; these become the OpenAPI contract the frontend types are generated from.
5. `modules/auth/` — `invitations.py`, `otp.py`, `passwords.py`, `service.py`, `router.py`. The exact flow is specified in `SPEC.md` §8.1 and must be followed step for step: the registration email comes from the invitation row, never from the submitted form, and the OTP goes to the invited address.
6. Refresh rotation with **reuse detection** — presenting an already-rotated token revokes the whole `family_id` and emails the user.
7. `integrations/email.py` (the `EmailSender` protocol and message type) and `integrations/smtp_client.py` (aiosmtplib, STARTTLS on 587, App Password, dry-run, SMTP status-code → typed error mapping). Plus the outbox row written in the same transaction as the invitation. `modules/auth/` depends on the protocol, never on the SMTP client — that seam is what makes a provider swap cheap (ADR-007).
   - Templates: `modules/notifications/templates/{invitation,otp_code}/` with `he` and `en` subject + body, rendered with Jinja2. Hebrew bodies need `Content-Type: text/html; charset=utf-8` and RTL markup in the HTML part.
   - `535` (bad App Password) is **not** retryable — dead-letter it and alert, rather than burning five attempts on a credential that will not fix itself.
8. Add the cross-module router contract to `.importlinter`, and turn on the coverage gate (90% on `auth`).
9. Frontend `features/auth/`: `InvitationLanding`, `OtpVerify`, `Register`, `Login`, `ForgotPassword` — Hebrew RTL, mobile-first.
10. Attach the `Authorization` header and refresh-on-401 at the marked seam in `api/client.ts`, with a single-flight guard so concurrent 401s trigger exactly one refresh.

**Done when**

- Playwright walks invite → OTP → register → login → refresh → logout in both `he` and `en`
- An expired or already-consumed invitation returns `410`
- 10 failed logins lock the account for 15 minutes, and the lock is audited
- A replayed refresh token revokes the entire family
- Unknown and known emails are indistinguishable in both response and timing

**Verify Phase 1 yourself**

```bash
docker compose -f infra/docker-compose.yml up -d db redis
cd backend
uv run alembic upgrade head
uv run python -m app.scripts.seed --reset
uv run pytest                      # 52 passed
```

**Currently running in this session** (background processes, safe to stop)

```
uvicorn app.main:app --host 127.0.0.1 --port 8000     # backend
npm run dev  (frontend, http://localhost:5173)        # Vite
docker compose -f infra/docker-compose.yml up -d db redis
```
