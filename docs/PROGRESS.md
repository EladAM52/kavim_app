# Kavim — Build Log

Running record of what has been built, what was decided, what broke, and what must be done before the next step.

**How this document works.** One section per phase, appended as work happens. Each entry states what was delivered, what was verified (with evidence, not assertion), what went wrong, and what is outstanding. The **Next step** section at the bottom is always current — read that first.

| | |
|---|---|
| Spec | [`SPEC.md`](SPEC.md) |
| Structure | [`../PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) |
| Conventions | [`../CLAUDE.md`](../CLAUDE.md) |
| Onboarding | [`ONBOARDING.md`](ONBOARDING.md) — concept primers, file map, testing map |
| Last updated | 2026-07-28 (session 11) |

---

## Status at a glance

| Phase | Scope | State |
|---|---|---|
| **0** | Foundation: repo, Docker, `core`, health endpoints, React shell with RTL/i18n, CI | ✅ **Complete and verified** |
| **1** | Data model, Alembic migration, seed script, integration test harness | ✅ **Complete and verified** |
| 2 | Auth: invite → OTP → register → login → refresh | ✅ **Complete and verified end to end**, including Playwright in both locales |
| 3 | Authorization + admin panel | ✅ **Complete and verified.** 3A backend, 3B admin UI, 16 admin e2e tests. The SPEC §13 "done when" needs the column editor and waits for Phase 5 |
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
| ~~Nothing committed yet~~ | Resolved in session 2 (`fb83fd9`) |

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
| ~~Nothing committed since `45224b9`~~ | Resolved — Phase 1 shipped in `fb83fd9` |

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

## Session 8 — 2026-07-27 · Phase 3A: authorization and the admin API

CLAUDE.md rule 2 — "never trust the client for authorization" — stops being a rule people
intend to follow and becomes one they cannot forget to follow.

**257 backend tests** (was 119), **85% coverage** overall and **95%** on `auth` +
`permissions`, 5/5 import contracts.

### Delivered

| Area | Files |
|---|---|
| Layer 3 | `core/permissions.py` — `column_is_editable`, pure and unit-tested against the seeded demo board |
| Cached resolver | `modules/auth/authz.py` — 5-minute TTL keyed on `(user, project)`, `invalidate_user`, `invalidate_all` |
| The dependency | `modules/auth/dependencies.py` — `Principal`, `PermissionRequirement`, `require_permission`, `require_authenticated` |
| Deny by default | `tests/security/test_all_routes_declare_permission.py` — five assertions plus a self-check |
| Shared schema | `schemas/common.py` — `SchemaBase`, `Page[T]`, keyset cursors, phone normalisation |
| Self-service | `modules/users/` — `GET`/`PATCH /users/me` |
| Administration | `modules/admin/` — `roles.py`, `users.py`, `invitations.py`, `audit_log.py`, twelve endpoints |
| Gates | CI coverage enforced for the first time; `.importlinter` extended to the two new modules |

### The decisions worth knowing

**`require_permission` is an object, not a closure — and that is what makes the security test
possible.** The test has to read the required permission back off the route table *without
executing the route*. FastAPI exposes a dependency as `.call` on its dependant node, so a
frozen dataclass hands the string over directly. A closure would bury it in `__closure__`,
retrievable only by cell position — meaning a refactor of the factory would silently break the
one test whose job is catching refactors.

**FastAPI 0.140 does not flatten included routers**, and finding that out was the difference
between a working test and a vacuous one. `app.include_router()` appends an `_IncludedRouter`
whose `original_router` holds the real routes, and a nested route's `.path` carries only its
own prefix. The obvious implementation — iterate `app.routes`, keep the `APIRoute`s — finds
**zero routes and passes unconditionally**. `test_the_walker_sees_every_published_route`
cross-checks the traversal against the OpenAPI path set, which FastAPI builds independently,
so the enumeration can never silently go quiet.

**The permission cache fails soft, and not for `rate_limit.py`'s reason.** That module fails
*open* — the control is skipped — which is defensible only because a database counter sits
behind every limit. Nothing is skipped here. This is a read-through cache over a query against
the source of truth: a miss, a Redis outage, and a corrupt value all take the same branch,
which is to ask PostgreSQL. A Redis outage costs latency and makes authorization *more*
current, not less. Failing closed would turn a Redis restart into every user seeing 403 on
every route, for no security gain at all. The argument is written into the module docstring
because the two files look alike and somebody will eventually try to harmonise them.

**Invalidate after the commit, never before.** Invalidating first is a live race: a concurrent
request misses the cache, resolves from the pre-commit state it can still see, and repopulates
the key with the stale value — which then survives the full five minutes.

**A matrix edit flushes the whole cache rather than enumerating the role's holders.**
Enumerating is cheaper and is *wrong*: if the same transaction also changed a role assignment,
the membership list misses somebody whichever side of the change it is read from.

**Layer 3 landed now rather than in Phase 5.** FR-210's permission trace has to show column
overrides, so deferring it would have meant shipping that endpoint incomplete. The function is
pure and imports nothing; only its enforcement call sites wait for the cell-write endpoint.

**`seed_roles` was deliberately not fixed.** It only ever *adds* rows, so a re-seed silently
restores a permission an administrator revoked. That looks like a bug and is the recovery path:
`DEFAULT_ROLE_MATRIX[SYSTEM_ADMIN]` is every permission, so `seed --reference` is the way back
from a stripped admin — which is what makes the lockout guard a convenience rather than the
only thing between an operator and a database shell. Making it reconcile-and-delete would
destroy every runtime matrix edit on every deploy. Documented at the site and pinned by two
tests in `test_seed_matrix_interaction.py`.

### Bugs found by running it

**1. Re-inviting an address with a live invitation was a 500.** `create_invitation` added the
new row and *then* revoked the old one, leaving two `pending` rows for one address at flush
time — exactly what the partial unique index `uq_invitations_pending_email` forbids. It never
surfaced in Phase 2 because nothing had yet re-invited anybody; FR-111's resend does it every
time. Fixed by revoking and flushing first, then inserting. A genuine production defect, not a
test artefact.

**2. Keyset pagination died on page two.** `encode_cursor` serialises with `default=str`, so a
timestamp came back as an ISO string and `WHERE (created_at, id) < ($1, $2)` asked PostgreSQL
to compare `timestamptz` with `text`. Not a silent coercion — `operator does not exist`, a 500.
`cursor_datetime` parses it back.

**3. Ruff and mypy disagreed about a dict comprehension.** `C416` wanted `dict()`, and `dict()`
over a SQLAlchemy `Row` sequence loses the type. An explicit loop satisfies both. Recorded
because the instinct is to add a `noqa` and move on.

**4. `_Base = SchemaBase` produced 24 mypy errors.** An assignment makes the name a *variable*,
so subclasses lose every field. The alias was replaced with a real rename.

**5. Beat had never dispatched anything, in any environment, ever.** Found by using the system
rather than by reading it — a real user sat on the OTP screen for two minutes waiting for mail
that was queued and going nowhere.

`celery -A app.workers.celery_app beat` imports that module and reads `conf.beat_schedule` off
the app it finds there. The schedule assigned itself onto the app from inside
`beat_schedule.py` — and **nothing imported `beat_schedule.py`**. Not `celery_app.py`, not
`include=`, not the worker. So beat read `{}`.

Every component was healthy and every component was right:

| | |
|---|---|
| Beat | Logged `beat: Starting...` — an empty schedule is a legal schedule |
| Worker | Registered `sweep_outbox` correctly (it *is* in `include=`) and waited for a message that was never coming |
| Outbox rows | `status=pending`, `next_attempt_at` in the past, correctly waiting for a sweeper |
| `/health/ready` | 200 — it checks Postgres and Redis, both fine |

No exception, no log line, no failed probe. The only observable symptom was a person who never
received an email. In production this is a **total registration outage that looks healthy from
every angle you would normally check**.

*Why two sessions of tests missed it.* Session 6 verified the sweeper by calling `sweep()`
directly and by running `--sweep` from the script. Both bypass Redis, beat, and the worker
entirely. Twenty tests proved the dispatch *logic* was correct — and it was, all of it. Nothing
asked whether anything ever calls it on its own. **Testing the function is not testing the
trigger**, and that generalises well beyond Celery.

*The fix, and a wrong turn worth recording.* The first attempt kept the `SWEEP_TASK_NAME` import
in `beat_schedule.py` and moved the assignment to the bottom of `celery_app.py` to dodge the
resulting cycle. That "worked" only when Python entered from the `celery_app` side, and raised
`ImportError` the moment a test imported `beat_schedule` first — an import graph that resolves
or explodes depending on entry point is the worst kind of working. `beat_schedule.py` is now
import-free of everything under `workers/` (task names are literals), the schedule is installed
in the existing `conf.update(...)` block, and `tests/unit/test_beat_schedule.py` asserts the
literals against the constants the tasks actually register under.

`app/workers/` went from **0% to 49%** coverage as a result — the boundary that had never been
tested is now the boundary with a regression test.

**6. Starting beat immediately exposed a duplicate-send bug.** Within two minutes of the
scheduler working for the first time, one outbox row produced **two real deliveries** and stayed
`pending` with `attempts=0`. It would have kept going every 30 seconds.

The chain: `smtp_client.send()` logs `email_sent` — including the subject — *after* the SMTP
transaction completes. On a Windows cp1252 console a Hebrew subject makes that `print()` raise
`UnicodeEncodeError`. `dispatch_row` caught only `EmailError`, so it escaped, the transaction
rolled back, and the row reverted to `pending` **after Gmail had accepted the message**.

Two fixes, because there are two faults:

- **`configure_logging` now forces UTF-8 on stdout and stderr**, with `errors="replace"`. A log
  line must never be able to take down the thing it is reporting on. `seed.py` and `invite.py`
  each carried a private copy of this fix, which is exactly why the worker — a third entry
  point — did not have it. It is central now.
- **`dispatch_row` catches `Exception`, not just `EmailError`**, and its docstring's promise that
  it never raises is finally true. After an unclassified error nobody knows whether the provider
  accepted the message; it retries, because a duplicate OTP is a smaller harm than a lost one,
  but through the normal attempt budget so it dead-letters instead of looping.

The second fix was itself incomplete on the first pass — the new branch retried without checking
the ceiling, so `attempts` climbed past `MAX_DELIVERY_ATTEMPTS` forever. The test caught it at 6.
Unbounded resend reached by a different route is still unbounded resend.

`.env` now defaults to `EMAIL_DRY_RUN=true` again. Real credentials plus real sending as the
standing default means every test invitation is a real email against a ~500/day ceiling, and
every `@example.com` fixture address is a bounce. Flipping it is deliberate.

### Probes — an assertion nobody has seen fail is not known to work

Four, all run and all reverted:

```
undeclared route added                 -> A1 FAILED and A5 FAILED  (correct)
mutation behind require_authenticated  -> A1 passed, A5 FAILED     (correct: escape hatch closed)
require_permission("user:mange")       -> ValueError at import     (correct)
require_permission()                   -> ValueError at import     (correct)
```

The second is the one that matters. Without A5, `require_authenticated()` would become a
universal exemption and A1 would degrade into "the author typed something".

### Verification evidence

```
ruff / format   All checks passed!
mypy --strict   Success: no issues found in 67 source files
import-linter   Contracts: 5 kept, 0 broken
pytest          257 passed          (was 119)
coverage        85% overall · 95% on modules/auth + core/permissions
```

Declared authorization, read off the live route table:

```
POST   /api/v1/auth/logout-all                              (authenticated)
GET    /api/v1/users/me                                     (authenticated)
PATCH  /api/v1/users/me                                     (authenticated)
GET    /api/v1/admin/permissions                            user:manage_permissions
GET    /api/v1/admin/roles                                  user:manage_permissions
PUT    /api/v1/admin/roles/{role_id}/permissions            user:manage_permissions
GET    /api/v1/admin/users                                  user:manage
PATCH  /api/v1/admin/users/{user_id}                        user:manage
POST   /api/v1/admin/users/{user_id}/force-logout           user:manage
GET    /api/v1/admin/users/{user_id}/effective-permissions  user:manage_permissions
POST   /api/v1/admin/invitations                            user:invite
GET    /api/v1/admin/invitations                            user:invite
POST   /api/v1/admin/invitations/{invitation_id}/resend     user:invite
DELETE /api/v1/admin/invitations/{invitation_id}            user:invite
GET    /api/v1/admin/audit-log                              audit:read
```

### Where SPEC diverges, and why

- **SPEC §6.1 amended.** It placed `require_permission` in `core/permissions.py`. The
  dependency needs `Depends(get_db)` and `app.models.user`, and the `core-independence`
  contract forbids that import. The contract is the stronger constraint; `core/permissions.py`
  keeps the rules and the pure resolvers, and the machinery lives in `modules/auth/`.
- **SPEC §13's Phase 3 "done when" cannot be met yet.** *"A manager grants a worker edit rights
  on one column; the worker can edit that one and is blocked on the rest, in UI and API"* needs
  the column editor (Phase 4) and the cell-write endpoint (Phase 5). The end-to-end
  demonstration moves to Phase 5 sign-off, where SPEC §11.3 scenario 6 already lives.
- **`/admin/notifications/deliveries`** appears under `admin` in §9.3, but §13 assigns the
  delivery log to Phase 7. Excluded by decision, not oversight.

### Phase 3A acceptance criterion, as actually met

- A worker receives 403 on every `/admin/*` route; an admin receives 200 on all of them.
- A role-matrix edit takes effect on the affected user's **very next request** — same token, no
  sleep, no re-login (FR-202).
- `GET /admin/users/{id}/effective-permissions?project_id=…` reports a worker as editable on
  `status` and not editable on `verified` for the seeded demo board (FR-205, FR-210).
- Authorization still enforces correctly with Redis unreachable.
- `tests/security/test_all_routes_declare_permission.py` is green, and demonstrably fails when
  a route is added without a declaration.

### Known gaps

| Gap | Consequence |
|---|---|
| **No admin UI** | Phase 3B. `features/admin/` is unwritten; no `usePermission` hook, no `RequirePermission` guard, no Table/Modal/Toggle primitives, no `admin` i18n namespace |
| **Still no Playwright** | The largest outstanding gap, and the only thing that would catch an RTL layout regression |
| Layer 2 not wired into `require_permission` | No project-scoped route exists yet. `project_param` arrives with `modules/projects` in Phase 4; adding an optional keyword then changes no call site |
| Layer 3 has no enforcement call site | The resolver is correct and tested; `cells.py` calls it in Phase 5 |
| `app/workers/` at 49% coverage | Was 0%. `celery_app` and the schedule are now pinned; `tasks_notifications`' Celery boundary (the `asyncio.run` wrapper) is still untested |
| The worker and beat must be started by hand locally | `celery -A app.workers.celery_app worker --pool=solo` and `… beat`. **`--pool=solo` is required on Windows** — the default prefork pool does not work there. Without both running, queued mail never leaves, which is exactly the defect above |
| A deploy running `seed --reference` undoes a deliberate matrix revocation | Documented and pinned by tests; check the deployment scripts before relying on FR-203 in production |
| `lint-imports` needs `PYTHONIOENCODING=utf-8` on a Windows console | Its progress output is Unicode and cp1252 kills it mid-run |

---

## Blocked / awaiting external action

| # | Item | Blocks | Owner | Status |
|---|---|---|---|---|
| ~~E1~~ | ~~**Gmail App Password**~~ | — | — | ✅ **Supplied and configured** in session 8. `.env` carries the real `SMTP_*` block with `EMAIL_ENABLED=true`, `EMAIL_DRY_RUN=false`, and the settings guard accepts it. **The supplied password must be rotated** — it was pasted into a chat transcript, so revoke it and replace it with one typed straight into `.env` |
| E1b | **Deliverability check against the plant's real mail domain** | Phase 7 acceptance | User | 🟡 **Half answered (session 11).** Mail sent from the production server reached an `@audiocodes.com` inbox and the recipient completed registration. Whether it lands in inbox or spam across other recipients and domains is still open — SPEC risk R13 |
| ~~E2~~ | ~~Twilio + Israeli sender registration~~ | — | — | **Dropped.** SMS deferred (SPEC §6.14.1, ADR-007) |
| ~~E3~~ | ~~Entra ID SSO within 12 months?~~ | — | — | ✅ **Answered: no.** Password auth is the only path. `auth_provider` / `external_idp_id` stay in the schema, unread |
| ~~E4~~ | ~~Retention period for quality records~~ | — | — | ✅ **Answered: 24 months.** `models/audit.py RETENTION_MONTHS` unchanged; partitioning stays deferred (SPEC R8) |
| E5 | **Existing quality checklists or forms** (photo or Excel of a real one) | Phase 4 default columns and templates | User | ⏳ **The one worth chasing now.** Longest lead time, biggest payoff — it turns generic templates into ones a line can use on day one |
| E6 | **Pilot cohort**: which line, which shift, how many workers | Phase 8 scope | User | ⏳ |
| E7 | **Plant Wi-Fi coverage** at the stations workers use | Phase 8 offline scope | User | ⏳ Determines how capable the offline queue has to be |
| E8 | Is there an **employee directory to import**, or is every user invited by hand? | Bulk invite, if it is needed at all | User | ⏳ Still open. The Phase 3B panel invites one address at a time, which is right for a 50-person pilot invited by hand and wrong for a directory import. Answer decides whether bulk invite is built |

---

## Next step

**Phase 4 — projects, groups, and the column engine.** Details at the end of session 10.

**Verify Phase 3 yourself**

```bash
docker compose -f infra/docker-compose.yml up -d db redis
cd backend
uv run alembic upgrade head          # no new migration in Phase 3
uv run python -m app.scripts.seed    # idempotent; --reset wipes and rebuilds
uv run pytest --cov                  # 257 passed, 85%
uv run uvicorn app.main:app --port 8000

cd ../frontend
npm run dev                          # then http://localhost:5173
npm run e2e                          # 30 passed, needs the backend above
```

In the browser, sign in as `admin@kavim.example.com` (`KavimDemo2026!`) and open **ניהול** in the
header: the user list, the matrix, invitations, and the audit log. Sign in as
`worker1@kavim.example.com` and there is no admin link at all; going to `/admin/users` directly
says why, and the API refuses the same request independently.

---

## Session 9 — 2026-07-28 · Playwright, and the visual check Phase 2 owed

The last two debts from Phase 2, both closed. **14 e2e tests** across two locales,
plus the first time anybody has looked at a rendered page.

### Delivered

| File | Role |
|---|---|
| `frontend/playwright.config.ts` | Two projects — `he-mobile` (Pixel 7, `he-IL`) and `en-desktop`. Every spec runs twice |
| `frontend/e2e/auth.spec.ts` | invite → OTP → register → reload → login, plus spent/invalid tokens, error copy, and a 320px layout check |
| `frontend/e2e/support/backend.ts` | The seam: shells out to the invite CLI for an invitation and the queued code |
| `frontend/tsconfig.e2e.json` | Its own project — specs are Node code, and `types: ["node"]` must not leak into browser code |
| `.github/workflows/ci.yml` | An `e2e` job: Postgres + Redis, migrate, seed, start the backend, run headless, upload traces on failure |
| `app/scripts/invite.py` | `--json` and `--otp EMAIL`, both reusing `invite_user` |

### The decisions worth knowing

**The specs import the app's own locale files.** A copy change then does not break the test —
but a *missing translation* does, which is the failure actually worth catching. It also means
the Hebrew assertions are written in Hebrew, against the same strings the user reads.

**No test-only endpoint, and no second database driver.** An e2e test has to start from a real
invitation and needs the code that was "emailed". Both come from the CLI, which refuses to run
in production and calls the same `invite_user` as `POST /admin/invitations`. A fixture that
minted invitations independently would drift from the real path; a `pg` dependency in the
frontend toolchain would duplicate the schema.

**CI runs with `EMAIL_DRY_RUN=true` and no credentials.** The specs read the code out of the
outbox rather than a mailbox, so nothing needs to be sent — and a CI job that *could* send real
mail would email a stranger every time somebody opened a pull request.

**Playwright starts Vite but deliberately not the backend.** A suite that silently boots an API
against whatever database happens to be configured is how a test run destroys development data.

**One worker, no parallelism.** The auth flow is inherently sequential — an invitation is
consumed exactly once — and parallel workers would interfere on shared rate-limit counters for
the sake of a few seconds.

### What the browser found that 33 component tests could not

RTL renders correctly: heading, labels, and brand at the start (right), toggle at the end
(left), required markers on the correct side, no horizontal overflow at 320px, 44px targets
intact.

**One real defect, visible only by looking.** `AuthLayout` wrapped the language toggle in a
`bg-brand-700` block, because the toggle is styled for the teal app header and its unselected
state is invisible on a pale background. The result was a hard-edged green slab floating on the
light auth page — the exact opposite of the "blend into the background, white pill on the
selected locale" the toggle was designed for two sessions ago.

Fixed properly rather than patched: `LanguageToggle` now takes a `tone`, and `onLight` uses a
barely-there `slate-200/60` track with a white pill. The wrapper is gone. This is precisely the
class of thing the component suite is structurally unable to see — it asserts behaviour, and
behaviour was never wrong.

### Two smaller things the run surfaced

- **`Field` renders `<label>סיסמה *</label>`.** An exact-match selector misses it and a
  substring match also matches `אימות סיסמה`, so `field()` anchors at the start. Worth knowing
  that the required marker is part of the accessible name.
- **The "no server English leaked" assertion only means anything in Hebrew.** The English
  translation legitimately reads "Email or password is incorrect", so a leak of the backend's
  own wording is indistinguishable from correct output. In Hebrew, any Latin text in that alert
  proves the client rendered `problem.detail` instead of looking up the code.

### Verification evidence

```
playwright     14 passed  (7 specs × he-mobile + en-desktop)
tsc -b         no errors  (three projects now: app, node, e2e)
eslint .       clean
vitest run     33 passed
```

### Known gaps

| Gap | Consequence |
|---|---|
| No e2e for the admin API | Phase 3B, where SPEC §11.3 scenario 6 (a worker blocked on a manager-only column) finally becomes testable |
| No `axe-core` pass | NFR-05 wants automated a11y assertions on every page-level component. The harness now exists to host them |
| Only Chromium | Firefox and WebKit are one config block each, deferred until there is a reason |

### Next step

**Phase 3B — the admin UI.** `features/admin/` with `UserTable`, `RoleMatrix`, `InvitationPanel`,
and `AuditLogView`; a `usePermission` hook and a `RequirePermission` route guard; the `admin`
i18n namespace in both locales; and the primitives none of it can be built without — Table,
Modal, Select, Toggle, Badge.

The RoleMatrix grid is the highest RTL risk in the project so far: the first wide
two-dimensional layout, 5 roles × 30 permissions, where a single physical CSS property escaping
the ESLint rule pushes the whole table sideways. The e2e harness is now in place to catch that.

---

## Session 10 — 2026-07-28 · Phase 3B: the admin UI

Phase 3 is complete. An administrator now does from a browser what previously needed a shell:
invite people, change roles, revoke sessions, edit the permission matrix, and read the audit log.

**269 backend tests** (was 257) · **46 frontend tests** (was 33) · **30 e2e tests** across two
locales (was 14) · all gates green.

### Delivered

| Area | Files |
|---|---|
| Regenerated contract | `api/generated/types.ts` — 1863 lines. Phase 3A's schemas were absent, so `AdminUserRow`, `RoleRow`, `PermissionRow`, `InvitationRow`, `AuditRow`, and `EffectivePermissionsTrace` had no frontend type at all |
| Admin client | `api/admin.ts` — twelve endpoints, keyset cursors, no page numbers |
| UI primitives | `components/ui/` — `Table`, `Modal`, `Select`, `Toggle`, `Badge`, plus `components/common/Ltr` |
| Permission reads | `hooks/usePermission.ts` (`usePermission`, `useAnyPermission`) and `features/auth/RequirePermission.tsx` (`RequirePermission` for routes, `PermissionGate` for subtrees) |
| Screens | `features/admin/` — `AdminLayout`, `AdminIndex`, `UserTable`, `UserEditModal`, `EffectivePermissionsModal`, `RoleMatrix`, `InvitationPanel`, `AuditLogView` |
| Shell | `components/layout/UserMenu.tsx` (the first sign-out in the UI), `ShellLayout.tsx`, nav links, breakpoint chip now `import.meta.env.DEV` only |
| Strings | `locales/{he,en}/admin.json` — full namespace; `forbidden.*` and `user.*` added to `common` |
| Support | `hooks/useApiError.ts`, `hooks/useDebounced.ts`, `lib/datetime.ts` (Jerusalem rendering, CLAUDE.md rule 8) |
| Tests | `usePermission.test.tsx`, `RoleMatrix.test.tsx`, `UserTable.test.tsx`, `e2e/admin.spec.ts`, `e2e/support/session.ts` |
| Email language | `InvitationCreate.locale` (optional) + a selector on the invite form, added after a live send arrived in the wrong language |

### The invitation language was the sender's browser, and nobody could change it

Found by sending a real invitation: it arrived in Hebrew, and nothing in the UI could have made
it English. `_accept_language()` read the admin's `Accept-Language` header — and
`adminApi.createInvitation` never set one, so the value came from the browser's own language
setting. The app's language toggle changes i18next and nothing else, so the one control that
looked like it should govern this had no effect on it at all.

The header was the wrong input in the first place. It describes the *sender*; the language that
matters belongs to the invitee, and on a plant where a Hebrew-speaking manager invites an
English-speaking contractor the default is wrong every time — invisibly, because the sender never
sees the mail.

`InvitationCreate` now takes an optional `locale`, the form has a selector defaulting to the
administrator's UI language, and the header stays as the fallback for the CLI and any client that
does not send one. Two integration tests pin both halves: an explicit locale beats a contradicting
header, and an absent one still follows it.

### The decisions worth knowing

**Matrix edits are staged, not live.** Every toggle is a whole-set PUT and the server flushes the
entire permission cache after each one, so saving per click would fire thirty flushes while an
administrator makes up their mind. The draft also makes the confirmation honest: it names the
roles that change and how many people hold them *before* anything happens, which is what
`RoleRow.user_count` exists for.

**The saves run sequentially, not `Promise.all`.** Five concurrent PUTs turn one cache-flush storm
into five, and make a partial failure impossible to report: the self-lockout guard can refuse the
third role after the first two committed. The screen refetches on settle — including on failure —
so it shows what is true rather than what was asked for.

**Four screens, four permissions, so the tab strip is built from what the user holds.** An auditor
has `audit:read` and nothing else. Rendering four tabs and letting three answer 403 reads as a
broken screen; rendering one reads as a boundary.

**A denied route explains itself instead of redirecting.** Bouncing to `/` is indistinguishable
from a broken link, and the next step after that is a support call. None of this is security — the
worker e2e test asserts both halves: the screen refuses, and the same request made anyway is still
refused by the server.

**`useAnyPermission` returns a boolean, never a filtered array.** A selector that built an array
would produce a new reference on every store write and re-render the whole shell.

**Native `<select>`, hand-rolled `<dialog>`.** The picker is native because Android renders it full
screen with 48px rows, which is what a gloved hand needs, and a custom listbox would need its own
RTL and focus handling to be worse. The modal is *not* native because jsdom does not implement
`showModal`, which would push every dialog assertion out of the component suite and into e2e.

### Bugs found by running it

**1. `/admin` rendered a blank page.** The redirect to the first tab lived in an
`if (pathname === '/admin')` branch inside `AdminLayout` — and a pathless layout route only matches
when one of its children does. With no index route, `/admin` matched the guard above it and
rendered its empty outlet, so `AdminLayout` never ran and the branch never executed. Fixed with a
real index route (`AdminIndex`). Found by the first e2e test, not by clicking around, because the
nav link goes to `/admin`.

**2. `vitest run` was reporting green with a file that never ran.** Vitest had no `include`, so its
default glob also collected `e2e/auth.spec.ts`; a Playwright spec loaded outside the Playwright
runner throws `Playwright Test did not expect test.describe() to be called here`. That is a failed
*suite*, and the summary line still reads `Tests 33 passed` — which is exactly what session 9
recorded. Scoped to `src/**` in `vite.config.ts`. A test count that cannot go down when a file
breaks is not a test count.

**3. `npm run lint` could not be run after `npm run e2e`.** ESLint had no ignore for
`playwright-report/`, so it tried to type-lint Playwright's own bundled report JavaScript and
aborted the entire run: *"You have used a rule which requires type information"*. Not a warning —
zero files linted. CI never hit it because it only uploads the report on failure and lints in a
different job.

### Probes — an assertion nobody has seen fail is not known to work

The two RTL assertions in `admin.spec.ts` are the reason the file exists, so both were made to
fail on purpose and then reverted:

```
sticky start-0 → sticky left-0   (matrix column)   -> he-mobile FAILED  right: expected "0px", got "auto"
overflow-x-auto removed          (Table wrapper)   -> he-mobile FAILED  page overflow 469px
```

The second is the classic RTL regression in its natural habitat: the grid is wider than a phone by
design, so if it does not scroll inside its own container, the document scrolls instead and the
whole layout shifts.

### Verification evidence

```
ruff / format     All checks passed!
mypy --strict     Success: no issues found in 67 source files
import-linter     Contracts: 5 kept, 0 broken
pytest            269 passed  (was 257)
tsc -b            no errors
eslint .          clean  (incl. the physical-CSS ban)
prettier --check  clean  (the glob now covers e2e/ too)
vitest run        46 passed   (was 33)
playwright        30 passed   (15 specs × he-mobile + en-desktop; was 14)
vite build        built in 2.05s
```

Bundle, gzipped: `index` 78.0 kB + `react` 32.1 kB + `i18n` 17.6 kB + `query` 15.9 kB + CSS
6.3 kB ≈ **150 kB** against the 250 kB budget in NFR-02. The admin area is lazy and costs a
signed-in worker nothing: `UserTable` 2.70 kB, `RoleMatrix` 2.18 kB, `InvitationPanel` 1.84 kB,
`AuditLogView` 1.54 kB, and the `date-fns-tz` chunk (8.70 kB) loads with them rather than at boot.

The permission boundary, live in a browser, in both locales:

```
admin@kavim.example.com   /admin -> /admin/users, 7 users listed, matrix saves and reverts
worker1@kavim.example.com no admin nav link · /admin/users -> "no access" · GET /admin/users -> 403
```

### Known gaps

| Gap | Consequence |
|---|---|
| No project picker on the permission trace | Layers 2 and 3 report "no project selected". `modules/projects` is Phase 4; the modal takes `project_id` already |
| Invitations cannot name projects | `POST /admin/invitations` accepts `project_ids`; the form sends `[]` because there is no project list to choose from yet |
| No `axe-core` pass | NFR-05 wants automated a11y assertions. Unchanged from session 9, and the admin screens are now the largest untested surface for it |
| No bulk invite | One address per submission. Whether that matters depends on E8 — see the blocked table |
| Matrix has no per-role save | Save applies every staged role at once. A refusal mid-loop leaves earlier roles saved, which the refetch shows honestly but does not undo |
| A **resent** invitation falls back to the header | `invitations` has no `locale` column, so the language chosen when the invitation was created cannot be recovered on resend. Persisting it is a migration; worth doing when the next one is needed anyway |
| The `invite` CLI is still Hebrew-only | Hardcoded `settings.DEFAULT_LOCALE`. Adding `--locale` is a two-line change, deferred because the UI now covers the real use |
| `admin` i18n bundle loads at boot | All four namespaces are static imports in `i18n.ts`. Lazy namespace loading is the Phase 8 bundle work |

### Next step

**Phase 4 — projects, groups, and the column engine.** `modules/projects` with the board column
types, group ordering, and saved views; `project_param` wired into `require_permission` so layer 2
finally has a call site; and the project picker that completes the FR-210 trace.

**Worth doing first:** answer **E5** in the blocked table. Phase 4 designs the default column set,
and a photo of one real quality checklist is what separates a generic template from one a line can
use on day one.

---

## Session 11 — 2026-07-28 · Deployment: live at srv1515969.hstgr.cloud/kavim

The first non-development deployment, behind the host's existing nginx, under a subpath. Six
defects, three of them found only by real traffic on a real host.
**286 backend tests** (was 269) · **50 frontend** (was 46) · 30 e2e.

The localhost setup is untouched and still works exactly as before — verified by rebuilding at the
root and re-running the full e2e suite.

### Delivered

| File | Role |
|---|---|
| `infra/docker-compose.prod.yml` | db · redis · minio · minio-init · backend · worker · beat, plus a one-shot `migrate` behind a profile |
| `infra/nginx/kavim.conf` | The `/kavim` location blocks, prefix stripping, WebSocket upgrade, asset caching |
| `infra/env.production.example` | The template to copy to `.env` on the server. No secrets, `CHANGE_ME` on each one, with the generator command beside it |
| `docs/DEPLOYMENT.md` | First deploy, updating, backups, and the gaps this deployment still has |
| `backend/Dockerfile` | New `frontend-build` stage; the `prod` image now contains the SPA at `app/static` |
| `core/config.py` | `APP_PUBLIC_PATH`, `refresh_cookie_path`, and a validator |
| `lib/basePath.ts` | `basePath` / `routerBasename` / `withBase`, derived from Vite's `BASE_URL` |

### The subpath is four problems, and only three of them are nginx's

`/kavim` rather than a host root touches the asset URLs, the client routes, the API base, and the
refresh cookie. nginx strips the prefix before proxying, which fixes the first three by letting the
application keep routing at `/` — it never learns where it is mounted.

**The cookie it cannot fix.** A browser matches a cookie's `path` against the address bar, not
against what the backend received. Left at `/api/v1/auth`, the cookie is stored and then never sent
back: login succeeds, and the next page load signs the user out. It presents as a token bug and is
not one — the token code is correct and the cookie simply never arrives. `APP_PUBLIC_PATH=/kavim`
makes the path `/kavim/api/v1/auth`, and a malformed value (`kavim`, `/kavim/`) is a startup error
rather than a mystery at 3am.

**`VITE_BASE_PATH` is a build argument, not an environment variable.** Vite writes the base into
every asset URL when it builds, so an image built for the root cannot be re-pointed at a subpath by
restarting it with a different env. That is why it is a Docker `ARG` wired to `APP_PUBLIC_PATH` in
the compose file, and why moving the mount point means a rebuild.

Everything derives from one value at runtime: `import.meta.env.BASE_URL` feeds `lib/basePath.ts`,
which feeds the router's `basename`, the API base, and the health probe. Localhost keeps `/` and is
byte-for-byte unaffected — verified by rebuilding at the root and running the full e2e suite again.

### Decisions

| Decision | Reasoning |
|---|---|
| **MinIO in the stack rather than relaxing the storage guard** | `STORAGE_BACKEND=local` is refused in production because a container filesystem is not durable — a rebuild loses every attachment. MinIO speaks S3, so Phase 6 works against it unchanged and real S3 later is a credentials change, not a data migration |
| **Not `APP_ENV=staging` to dodge the guards** | It would also turn off `Secure` on the refresh cookie and publish `/docs`, on an internet-facing host. The guards are right; the configuration had to satisfy them |
| **nginx strips the prefix; the app stays root-relative** | The alternative — `root_path` and prefix-aware routing throughout — puts the deployment topology into application code, where every future route has to remember it |
| **Only the backend publishes a port, on `127.0.0.1`** | Postgres, Redis, and MinIO are reachable on the compose network and nowhere else. A database exposed to the internet is the most common way a small deployment is lost |
| **`migrate` is a one-shot command, not part of `up`** | A container that restarts at 3am must never rewrite the schema on its own |
| **The SPA is baked into the backend image** | One origin, one port, no CORS, no second web server (SPEC §5.5). The cost is that a frontend-only change still rebuilds the backend image |

### Verification

```
ruff / format   All checks passed!
mypy --strict   no issues in 67 source files
import-linter   Contracts: 5 kept, 0 broken
pytest          275 passed  (was 269)
tsc / eslint / prettier   clean
vitest          50 passed   (was 46)
playwright      30 passed   — root build, unchanged
```

A subpath build really does rewrite the asset URLs:

```
VITE_BASE_PATH=/kavim/  ->  src="/kavim/assets/index-C-KXop3Y.js"
default                 ->  src="/assets/index-DjkWXwqC.js"
```

*(Windows note: Git Bash rewrites `/kavim/` into a Windows path, so that check has to run in
PowerShell or with `MSYS_NO_PATHCONV=1`. It cost a confusing minute; it is in `DEPLOYMENT.md`.)*

### The deploy, and the three things it found

**Kavim is live at `https://srv1515969.hstgr.cloud/kavim`.** Verified in a browser: sign in, reload
and stay signed in, invite a colleague, and that person received the mail and registered. That
exercises the subpath end to end — asset base, router basename, API base, and the refresh cookie's
path — plus the outbox sweeper delivering real Gmail from the server rather than from a laptop.

Three defects, all found by deploying rather than by reading:

**1. Compose never read the repo-root `.env`.** The documented `up` aborted immediately with
*"required variable POSTGRES_PASSWORD is missing a value"* — a variable plainly present in the file
the operator had just filled in. Compose resolves `${VAR}` from a `.env` in the *project directory*,
which defaults to the folder holding the compose file (`infra/`), not the repo root. `env_file:
../.env` had masked the difference in review: it passes variables *into* containers and does resolve
relative to the compose file, so the two mechanisms look interchangeable and are not. Every command
in the runbook now passes `--env-file .env`. The `:?` guards did their job — the alternative was
Postgres initialising with a blank password.

**2. Port 8000 was already taken**, by another application on the same box. Moved to 4000 — which
turned out to be the port the server's existing nginx `/kavim/` block *already* proxied to, so no
nginx change was needed at all. The snippet in `infra/nginx/` remains for a fresh host. Worth
recording: the nginx port and `BACKEND_PORT` are two places that must agree and nothing enforces it,
because nginx cannot read `.env`.

**3. There was no way to create the first user.** The real one. A fresh production database has
reference data and no accounts, and every route in needs somebody already inside:
`POST /admin/invitations` needs a bearer token; `app.scripts.invite` refuses in production by design
*and* needs an existing admin as the inviter; `seed.py` refuses demo users. Each guard is correct on
its own and together they lock the door from the inside.

`app/scripts/bootstrap_admin.py` is the one bootstrap door. It creates a single SYSTEM_ADMIN and
then **disables itself** — the guard is the state of the database, not `APP_ENV`, so once any active
user holds `user:manage_permissions` it refuses. It also refuses an address that already exists, so
it cannot reactivate a deactivated account into an administrator by accident. Password prompted or
read from stdin, never an argument. Audited, with the new account as its own actor. Four integration
tests, including the self-disable, because an unexercised guard is an assumption.

**286 backend tests** by the end of the session (was 275).

### Still open after the deploy

- **E1 — the Gmail App Password has still not been rotated.** It was pasted into a chat transcript
  in session 8, and it is now on a server as well as a laptop. Blocked on Elad's Google account
  challenge (*"Google couldn't verify this account belongs to you"*), which is unrelated to this
  system and cannot be resolved from it.
- **E1b — deliverability**: mail from the server reaches an `@audiocodes.com` inbox, which answers
  the *delivery* half. Whether it lands in the inbox or in spam for other recipients on other
  domains is still SPEC risk R13.
- Nothing is backed up. `pgdata` now holds real accounts and a real audit log.

### Operator access, and three more defects the server found

Swagger and pgAdmin were both unreachable, deliberately. Both work now without publishing anything
new: `API_DOCS_ENABLED` mounts the docs at all (off by default in production) and nginx demands HTTP
Basic for those three paths — two switches, both of which must be on. Postgres publishes
`127.0.0.1:5434`, for pgAdmin's own SSH-tunnel tab, so the database is never on a public port.

**4. The production CSP blanked the Swagger page it had just started serving.** The password prompt
worked and the page came up empty. The security-header middleware tested `is_production` *before*
the docs branch, so `/docs` inherited the SPA's `script-src 'self'` and the browser refused
Swagger's CDN bundle. The comment above `_DOCS_PATHS` asserted *"production never reaches here:
docs_url/redoc_url are None there"* — true when written, false the moment the docs became optional
rather than absent. **A comment stating an invariant is only as good as the change that breaks it.**

**5. `root_path` took the whole app down.** Added for the Swagger schema URL, and precisely
backwards: it tells Starlette the prefix *is present* in incoming paths, while nginx strips `/kavim`
before proxying. Every asset 404'd — `/kavim/assets/…` returned 200 — and the SPA rendered blank.
The real fix is far narrower: register the two docs pages by hand with
`openapi_url = APP_PUBLIC_PATH + "/openapi.json"` and leave the schema route at the root, where
nginx delivers it.

*How that shipped.* The probe written to verify it called `importlib.reload()` on the module whose
`settings` it had just patched, re-binding the name and undoing the patch — so both runs tested the
default and reported a pass. **A probe that cannot fail is worse than no probe**, because it is
believed. The server's own request log diagnosed it in one line. Two tests now drive `create_app`
through httpx at both `APP_PUBLIC_PATH` values.

`tests/security/test_all_routes_declare_permission.py` then refused the two hand-registered pages
for declaring no authorization — its second real catch. They are in `PUBLIC_ROUTES` with the
reasoning beside them.

**6. `worker` and `beat` reported "unhealthy" while working correctly.** Both inherit the backend
image's HTTP healthcheck and neither runs a web server. An indicator that is always red is one
nobody reads. The worker now answers `celery inspect ping`; beat has none, because a scheduler has
nothing honest to probe and Docker already knows whether the process is alive.

**A stray development stack was running on the server** — from a `-f infra/docker-compose.yml`
invocation — including Redis published on `0.0.0.0:6379` with no password, which is remote code
execution rather than an information leak. Removed. The two compose files differ by one word in the
middle of a long command, and the development one is the one in the README.

**286 backend tests.**

### Known gaps

| Gap | Consequence |
|---|---|
| **Production secrets were exposed in a chat transcript and not rotated** | `SECRET_KEY`, `POSTGRES_PASSWORD`, the Gmail App Password, and the MinIO root credentials were pasted in session 11 and left in place by decision. `SECRET_KEY` alone mints valid admin tokens. Rotation steps are in that session's exchange; treat all four as compromised until they change |
| No automated backups | `pg_dump` is documented and manual. `pgdata` holds every quality record and a 24-month audit retention requirement |
| No CI/CD to the server | Deploys are `git pull` and a rebuild, by hand, over SSH |
| MinIO is single-node | Survives container rebuilds, not disk loss |
| No staging environment | A bad release is discovered in production — and three of the six defects above were |
| `/health/ready` is public | Reachable through the catch-all; names which dependency is down. Restricting it is a commented block in `kavim.conf` |
| Swagger is reachable in production | Deliberate, behind `API_DOCS_ENABLED=true` plus HTTP Basic. It publishes the API map to anyone holding those credentials, and **"Try it out" runs against production data** |
| No automated test loads the app under `/kavim` | The units pin the cookie path, the base joins, and the schema link; the deploy exercised the rest by hand. Every subpath defect so far was found by a browser, not a test |
