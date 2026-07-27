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
| 2 | Auth: invite → OTP → register → login → refresh | ⬜ Next |
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
| `admin@kavim.local` | System admin |
| `manager@kavim.local` | Line manager |
| `supervisor@kavim.local` | Shift supervisor |
| `worker1@kavim.local` … `worker3@kavim.local` | Worker |
| `auditor@kavim.local` | Viewer / auditor |

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

## Next step — Phase 2: Authentication

Nothing external blocks this. SendGrid runs in sandbox mode, so the full flow is
testable without a verified sender domain (E1).

**To do**

1. Commit and push Phase 0 + Phase 1 (see the gap table above — nothing since `45224b9`).
2. `core/security.py` additions: JWT encode/decode, the short-lived `registration_ticket`, refresh-token rotation helpers.
3. `core/rate_limit.py` — Redis token bucket: login 10 per 15 min per IP *and* per email, OTP verify 5 per code, OTP request 3 per 15 min per email.
4. `schemas/auth.py` — Pydantic request/response models; these become the OpenAPI contract the frontend types are generated from.
5. `modules/auth/` — `invitations.py`, `otp.py`, `passwords.py`, `service.py`, `router.py`. The exact flow is specified in `SPEC.md` §8.1 and must be followed step for step: the registration email comes from the invitation row, never from the submitted form, and the OTP goes to the invited address.
6. Refresh rotation with **reuse detection** — presenting an already-rotated token revokes the whole `family_id` and emails the user.
7. `integrations/sendgrid_client.py` with sandbox mode, plus the outbox row written in the same transaction as the invitation.
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
