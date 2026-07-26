# Kavim — Project Structure

Companion to [`docs/SPEC.md`](docs/SPEC.md). Every service named in SPEC §6 has a home folder here.

Two rules govern the whole layout:

1. **Backend is organized by module, not by file type.** Everything about tasks lives in `modules/tasks/` — router, service, cell logic, ordering, queries. Not scattered across `routers/`, `services/`, `utils/`.
2. **Frontend is organized by feature, not by file type.** Everything about the board lives in `features/board/` and `components/board/`.

Both exist for the same reason: when you change how cell editing works, you open one folder.

---

## 1. Full tree

```
kavim/                                      # repo root
│
├── .github/
│   └── workflows/
│       ├── ci.yml                          # lint · typecheck · test · build · migration round-trip
│       └── e2e.yml                         # Playwright in he + en against docker compose
│
├── docs/
│   ├── SPEC.md                             # master specification — refined each step
│   ├── PROGRESS.md                         # build log: done · pending · blocking
│   └── adr/                                # architecture decision records, one file each
│       ├── 001-fastapi-only.md
│       ├── 002-react-spa.md
│       ├── 003-postgresql.md
│       ├── 004-hybrid-column-storage.md
│       ├── 005-transactional-outbox.md
│       └── 006-modular-monolith.md
│
├── backend/
│   ├── alembic/
│   │   ├── versions/                       # one migration per change, reviewed up AND down
│   │   ├── env.py                          # async engine wiring, imports all models
│   │   └── script.py.mako
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                         # app factory, middleware, router mount, static mount
│   │   │
│   │   ├── core/                           # SPEC §6.1 — imports nothing from modules/
│   │   │   ├── config.py                   # pydantic-settings; every env var declared and typed
│   │   │   ├── database.py                 # async engine, sessionmaker, get_db dependency
│   │   │   ├── security.py                 # argon2id, JWT, token/OTP generation + hashing
│   │   │   ├── permissions.py              # permission registry, role matrix, resolver
│   │   │   ├── enums.py                    # ALL shared enums — lives here, not in
│   │   │   │                               # models/, because core.permissions needs
│   │   │   │                               # RoleKey and core may not import models
│   │   │   ├── time.py                     # utc_now, local_today, quiet-hours window
│   │   │   ├── rate_limit.py               # Redis token bucket
│   │   │   ├── redis.py                    # connection pool, cache helpers
│   │   │   ├── exceptions.py               # error hierarchy + RFC 7807 handlers
│   │   │   ├── logging.py                  # structlog JSON, request-id contextvar
│   │   │   ├── i18n.py                     # locale resolution for outbound email/SMS
│   │   │   ├── pagination.py               # cursor pagination helpers
│   │   │   └── middleware.py               # request-id, timing, CORS, security headers
│   │   │
│   │   ├── scripts/                        # python -m app.scripts.<name>
│   │   │   └── seed.py                     # reference data + demo board. Lives here,
│   │   │                                   # not infra/, because it imports the app
│   │   │
│   │   ├── models/                         # SQLAlchemy 2.0 ORM, one file per aggregate
│   │   │   ├── base.py                     # DeclarativeBase, naming convention, mixins
│   │   │   ├── site.py                     # sites, lines
│   │   │   ├── user.py                     # users, notification_preferences
│   │   │   ├── role.py                     # roles, permissions, role_permissions, user_roles
│   │   │   ├── auth.py                     # invitations, otp_codes, refresh_tokens, reset_tokens
│   │   │   ├── project.py                  # projects, project_members, groups, saved_views
│   │   │   ├── column.py                   # board_columns
│   │   │   ├── task.py                     # tasks, task_assignees, cell_history, dependencies
│   │   │   ├── comment.py                  # comments
│   │   │   ├── attachment.py               # attachments
│   │   │   ├── notification.py             # outbox, deliveries, in_app_notifications
│   │   │   └── audit.py                    # audit_log
│   │   │
│   │   ├── schemas/                        # Pydantic v2 request/response — the OpenAPI source
│   │   │   ├── common.py                   # Page[T], ErrorResponse, CursorParams
│   │   │   ├── auth.py  user.py  admin.py
│   │   │   ├── project.py  column.py
│   │   │   ├── task.py  cell.py
│   │   │   ├── comment.py  attachment.py
│   │   │   ├── notification.py  report.py
│   │   │   └── realtime.py                 # WebSocket event envelopes
│   │   │
│   │   ├── modules/                        # SPEC §6.2–6.12 — hard boundaries between these
│   │   │   ├── auth/
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py              # login, refresh rotation, reuse detection
│   │   │   │   ├── invitations.py          # create, validate, consume
│   │   │   │   ├── otp.py                  # generate, send, verify, rate limit
│   │   │   │   └── passwords.py            # reset flow, policy check
│   │   │   ├── users/
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py
│   │   │   │   └── preferences.py          # notification preference matrix
│   │   │   ├── admin/
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py              # user management, force logout
│   │   │   │   └── roles.py                # role × permission matrix editing
│   │   │   ├── projects/
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py              # project CRUD, membership, archive
│   │   │   │   ├── columns.py              # the column engine — definitions, types, validation
│   │   │   │   ├── groups.py
│   │   │   │   ├── templates.py
│   │   │   │   └── views.py                # saved views
│   │   │   ├── tasks/
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py              # CRUD, assignment, archive, bulk
│   │   │   │   ├── cells.py                # per-type validation, column auth, version check
│   │   │   │   ├── ordering.py             # fractional indexing + rebalance
│   │   │   │   ├── queries.py              # board read: tasks+subtasks+cells, filters, sort
│   │   │   │   └── dependencies.py         # blocked-by links, cycle detection
│   │   │   ├── comments/
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py
│   │   │   │   └── mentions.py             # parse @mentions, validate membership
│   │   │   ├── notifications/
│   │   │   │   ├── router.py               # in-app feed, read state
│   │   │   │   ├── service.py              # recipient resolution, preference filter
│   │   │   │   ├── outbox.py               # write, claim (SKIP LOCKED), dispatch, retry
│   │   │   │   ├── webhooks.py             # SendGrid + Twilio, signature-verified
│   │   │   │   └── templates/
│   │   │   │       ├── invitation/         # subject.he.txt  body.he.html  (+ .en)
│   │   │   │       ├── task_assigned/
│   │   │   │       ├── comment_mention/
│   │   │   │       ├── task_overdue/
│   │   │   │       ├── password_reset/
│   │   │   │       ├── otp_code/
│   │   │   │       └── daily_digest/
│   │   │   ├── files/
│   │   │   │   ├── router.py               # presign, confirm, download, delete
│   │   │   │   ├── service.py
│   │   │   │   └── thumbnails.py           # Pillow, 400px + 1200px variants
│   │   │   ├── realtime/
│   │   │   │   ├── ws.py                   # endpoint, auth-as-first-message, subscribe
│   │   │   │   ├── hub.py                  # connection registry, project rooms, presence
│   │   │   │   └── listener.py             # Postgres LISTEN/NOTIFY → local fan-out
│   │   │   ├── audit/
│   │   │   │   ├── router.py               # filtered read
│   │   │   │   └── service.py              # write_audit(), called inside domain transactions
│   │   │   └── reports/
│   │   │       ├── router.py
│   │   │       ├── service.py              # aggregate queries
│   │   │       └── exporters.py            # CSV (UTF-8 BOM) + XLSX (RTL sheets)
│   │   │
│   │   ├── integrations/                   # SPEC §6.14 — the only place external SDKs appear
│   │   │   ├── sendgrid_client.py
│   │   │   ├── twilio_client.py
│   │   │   └── storage.py                  # local disk (dev) | S3-compatible (prod)
│   │   │
│   │   ├── workers/                        # SPEC §6.13
│   │   │   ├── celery_app.py               # broker config, request-id propagation
│   │   │   ├── beat_schedule.py            # the cron table
│   │   │   ├── tasks_notifications.py      # outbox sweep, per-channel dispatch
│   │   │   ├── tasks_digests.py            # daily digest builder
│   │   │   ├── tasks_maintenance.py        # token cleanup, soft-delete purge, rebalance
│   │   │   ├── tasks_exports.py            # large CSV/XLSX generation
│   │   │   └── tasks_media.py              # thumbnail generation
│   │   │
│   │   └── static/                         # frontend/dist mounted here in production
│   │
│   ├── tests/
│   │   ├── conftest.py                     # app client; postgres container, migrated
│   │   │                                   # schema, transaction-per-test rollback
│   │   ├── factories.py                    # make_user/_project/_task/_column/_invitation
│   │   ├── unit/                           # config, logging, health, ordering, validation
│   │   ├── integration/                    # schema guarantees against real Postgres
│   │   ├── security/
│   │   │   └── test_all_routes_declare_permission.py   # mechanical FR-209 enforcement
│   │   └── api/                            # endpoint contract tests
│   │
│   ├── pyproject.toml                      # deps + ruff + mypy + pytest + coverage config
│   ├── uv.lock
│   ├── alembic.ini
│   ├── .importlinter                       # module boundary rules
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── main.tsx                        # root render, providers, DirectionProvider
│   │   ├── App.tsx
│   │   ├── router.tsx                      # routes + permission guards + lazy boundaries
│   │   │
│   │   ├── api/
│   │   │   ├── generated/types.ts          # openapi-typescript output — NEVER hand-edited
│   │   │   ├── client.ts                   # fetch wrapper: auth, refresh-on-401, problem+json
│   │   │   ├── websocket.ts                # reconnect w/ backoff, polling fallback
│   │   │   └── hooks/                      # useProjects, useBoard, useUpdateCell, …
│   │   │
│   │   ├── components/
│   │   │   ├── ui/                         # shadcn primitives (button, dialog, sheet, …)
│   │   │   ├── layout/
│   │   │   │   ├── AppShell.tsx
│   │   │   │   ├── ResponsiveShell.tsx     # picks desktop vs mobile layout
│   │   │   │   ├── Sidebar.tsx  TopBar.tsx  BottomNav.tsx
│   │   │   ├── board/
│   │   │   │   ├── BoardGrid.tsx           # ≥768px: TanStack Table, virtualized
│   │   │   │   ├── BoardCardList.tsx       # <768px: card list (SPEC §10.4)
│   │   │   │   ├── ColumnHeader.tsx  GroupHeader.tsx  RowDragHandle.tsx
│   │   │   │   └── cells/                  # one editor per column type
│   │   │   │       ├── StatusCell.tsx  PersonCell.tsx  DateCell.tsx
│   │   │   │       ├── TimelineCell.tsx  TextCell.tsx  LongTextCell.tsx
│   │   │   │       ├── NumberCell.tsx  DropdownCell.tsx  CheckboxCell.tsx
│   │   │   │       ├── RatingCell.tsx  FileCell.tsx  LinkCell.tsx
│   │   │   │       └── index.ts            # type → component registry
│   │   │   ├── forms/                      # FormField, DatePicker (RTL), PersonPicker
│   │   │   └── common/                     # EmptyState, ErrorBoundary, Skeleton,
│   │   │                                   # ConfirmDialog, OfflineBanner, ConflictDialog
│   │   │
│   │   ├── features/
│   │   │   ├── auth/                       # InvitationLanding, OtpVerify, Register,
│   │   │   │                               # Login, ForgotPassword, ResetPassword
│   │   │   ├── projects/                   # ProjectList, ProjectSettings,
│   │   │   │                               # ColumnManager, MemberManager, TemplatePicker
│   │   │   ├── board/                      # BoardView, FilterBar, SortMenu,
│   │   │   │                               # ViewSwitcher, SavedViews, KanbanView
│   │   │   ├── tasks/                      # TaskSheet, SubtaskList, MyTasks,
│   │   │   │                               # BulkActionBar, CellHistory
│   │   │   ├── comments/                   # CommentFeed, CommentComposer,
│   │   │   │                               # MentionInput, AttachmentUploader, CameraCapture
│   │   │   ├── admin/                      # UserTable, RoleMatrix, InvitationPanel,
│   │   │   │                               # AuditLogView, DeliveryLog
│   │   │   ├── notifications/              # NotificationBell, NotificationList,
│   │   │   │                               # PreferenceMatrix, QuietHours
│   │   │   └── reports/                    # Dashboard, CompletionChart,
│   │   │                                   # WorkloadChart, ExportDialog
│   │   │
│   │   ├── hooks/
│   │   │   ├── useAuth.ts  usePermission.ts
│   │   │   ├── useWebSocket.ts  useBreakpoint.ts
│   │   │   ├── useOfflineQueue.ts  useDirection.ts
│   │   │
│   │   ├── stores/
│   │   │   ├── auth.ts                     # access token in MEMORY only (SPEC §8.2)
│   │   │   └── ui.ts                       # sidebar, active view, filters
│   │   │
│   │   ├── locales/
│   │   │   ├── he/                         # common auth board admin notifications errors
│   │   │   └── en/                         # same namespaces
│   │   │
│   │   ├── lib/
│   │   │   ├── rtl.ts                      # direction helpers, icon mirroring
│   │   │   ├── dates.ts                    # UTC ↔ Asia/Jerusalem, Hebrew locale
│   │   │   ├── permissions.ts              # client-side gate (UX only; server is authority)
│   │   │   ├── format.ts                   # numbers, phones, file sizes
│   │   │   ├── offlineQueue.ts             # IndexedDB queue + background sync
│   │   │   └── validation.ts               # zod schemas mirroring Pydantic
│   │   │
│   │   ├── styles/
│   │   │   └── index.css                   # Tailwind v4 entry + @theme design tokens
│   │   │                                   # (v4 is CSS-first — there is no tailwind.config.ts)
│   │   └── vite-env.d.ts                   # typed import.meta.env
│   │
│   ├── public/
│   │   ├── manifest.webmanifest            # PWA: Hebrew name, standalone, icons
│   │   ├── icons/                          # 192, 512, maskable, apple-touch
│   │   └── fonts/                          # Heebo (he), Inter (en), self-hosted
│   │
│   ├── e2e/                                # Playwright specs, run in he AND en
│   │   ├── auth-invitation-flow.spec.ts
│   │   ├── board-cell-editing.spec.ts
│   │   ├── mobile-worker-flow.spec.ts
│   │   ├── realtime-two-users.spec.ts
│   │   ├── permissions-denial.spec.ts
│   │   └── offline-sync.spec.ts
│   │
│   ├── index.html
│   ├── vite.config.ts                      # proxy /api,/ws,/health → :8000; build + vitest
│   ├── tsconfig.json                       # solution file: references app + node
│   ├── tsconfig.app.json                   # browser code; NO node types (strict)
│   ├── tsconfig.node.json                  # vite.config.ts only; node types
│   ├── eslint.config.js                    # incl. the physical-CSS ban + no dangerouslySetInnerHTML
│   ├── .prettierrc.json
│   ├── playwright.config.ts                # (Phase 8)
│   ├── package.json
│   └── Dockerfile                          # deps → dev | build | dist stages
│
├── infra/
│   ├── docker-compose.yml                  # db + redis by default; app/frontend behind profiles
│   ├── docker-compose.prod.yml             # prod: no bind mounts, no reload, replicas
│   ├── postgres/init/01-extensions.sql     # pgcrypto citext pg_trgm btree_gin, on first init
│   ├── caddy/Caddyfile                     # automatic TLS for VM/on-prem deploys
│   └── scripts/
│       ├── backup.sh                       # pg_dump custom format, encrypted
│       ├── restore.sh                      # documented restore path (drill quarterly)
│       └── wait_for_db.sh                  # entrypoint guard before migrations
│
├── .env.example                            # every variable from SPEC §12.1, dummy values
├── .gitignore                              # .env  .idea/  __pycache__  node_modules  dist
├── .editorconfig
├── .gitleaks.toml
├── PROJECT_STRUCTURE.md                    # this file
├── CLAUDE.md                               # conventions for AI-assisted work in this repo
└── README.md                               # what it is, how to run it in 5 commands
```

---

## 2. Creation order

Each step depends on the one before it. Following this order means nothing is ever built against a foundation that does not exist yet — the usual cause of a half-day of rework.

| # | Step | Creates | Why it must come here |
|---|---|---|---|
| **1** | Repo hygiene | `git init`, `.gitignore`, `.editorconfig`, `README.md`, `CLAUDE.md`, delete `main.py`, ignore `.idea/` | `.gitignore` before the first commit. A `.env` or `node_modules` committed once is in history forever |
| **2** | Documentation | `docs/SPEC.md`, `docs/adr/` | The spec is the contract everything else implements. Written first, refined continuously |
| **3** | Infrastructure | `infra/docker-compose.yml`, `.env.example`, `wait_for_db.sh` | Postgres and Redis must be reachable before any code needs them. `.env.example` forces every variable to be named before code reads it |
| **4** | Backend skeleton | `backend/pyproject.toml`, `app/main.py`, `app/core/*`, `Dockerfile`, `/health/live`, `/health/ready` | `core` is what every module imports. Config, database session, logging, and error handling exist before the first feature. Health endpoints make the container orchestratable from day one |
| **5** | Data model | `app/models/*`, `alembic/`, first migration | Models before services — a service written against a guessed schema gets rewritten. One complete migration is cleaner to review than fifteen incremental ones during initial design |
| **6** | Seed + test harness | `infra/scripts/seed.py`, `tests/conftest.py`, `tests/factories.py` | Realistic data and working fixtures before feature work, so every feature is testable the moment it is written rather than "tested later" |
| **7** | Auth module | `app/modules/auth/*`, `app/schemas/auth.py`, `core/permissions.py` | Every other module depends on `require_permission` and an authenticated user. Building features first means retrofitting auth into each one |
| **8** | Authorization + admin | `app/modules/admin/*`, `app/modules/users/*`, `app/modules/audit/*` | Roles and the permission matrix must exist before feature endpoints can declare which permission they require |
| **9** | Domain modules | `projects/` → `tasks/` → `comments/` → `files/` | Strict order: a task needs a project and its column definitions; a comment needs a task; an attachment needs a task or comment. Reversing this means stubbing parents |
| **10** | Async + realtime | `app/workers/*`, `app/integrations/*`, `app/modules/notifications/*`, `app/modules/realtime/*` | Notifications need domain events to react to. WebSocket needs entities to broadcast about. Both are meaningless before step 9 |
| **11** | Frontend foundation | `frontend/` scaffold, `tailwind.config.ts`, `styles/tokens.css`, `locales/`, `lib/rtl.ts`, `hooks/useDirection.ts`, `AppShell` | **RTL and i18n before the first feature component.** This is the single most order-sensitive step in the list: retrofitting RTL onto a built data grid means auditing every component for physical properties and every icon for direction |
| **12** | API type generation | `api/generated/types.ts`, `api/client.ts`, `api/hooks/` | Generated from the backend OpenAPI, so it needs step 9 complete. Everything downstream is then type-checked against the real API |
| **13** | Frontend features | `features/auth/` → `features/projects/` → `components/board/` + `features/board/` → `features/tasks/` → `features/comments/` → `features/admin/` → `features/notifications/` → `features/reports/` | Mirrors the backend order. Auth first because every other screen sits behind a login |
| **14** | Mobile + PWA | `BoardCardList`, `BottomNav`, `TaskSheet`, `manifest.webmanifest`, `lib/offlineQueue.ts`, service worker | The mobile layout is a swap of a working desktop layout, so desktop must work first. The offline queue needs real mutations to queue |
| **15** | End-to-end tests | `frontend/e2e/*` | Need working flows across both tiers |
| **16** | CI | `.github/workflows/ci.yml`, `e2e.yml`, `.gitleaks.toml`, `.importlinter` | Runs everything above. Added once there is something to run, but before the first deploy |
| **17** | Production deploy | `docker-compose.prod.yml`, `caddy/Caddyfile`, `backup.sh`, `restore.sh` | Last, because it deploys the finished thing. `restore.sh` is written and drilled here, not during the first incident |

### Mapping to the roadmap

| Roadmap phase (SPEC §13) | Steps |
|---|---|
| 0 — Foundation | 1–4, 11, 16 |
| 1 — Data model | 5–6 |
| 2 — Auth | 7 |
| 3 — Authorization + admin panel | 8 |
| 4 — Projects and column engine | 9 (projects), 12, 13 (projects) |
| 5 — Tasks and cells | 9 (tasks), 13 (board, tasks) |
| 6 — Collaboration | 9 (comments, files), 10 (realtime), 13 (comments) |
| 7 — Notifications | 10 (workers, integrations, notifications), 13 (notifications) |
| 8 — Mobile, PWA, reports | 14, 13 (reports), 15, 17 |

---

## 3. Conventions

### Naming

| Kind | Convention | Example |
|---|---|---|
| Python files, functions | `snake_case` | `update_cell`, `ordering.py` |
| Python classes | `PascalCase` | `BoardColumn`, `TaskCellHistory` |
| Database tables | `snake_case` plural | `board_columns`, `task_assignees` |
| Database columns | `snake_case`; foreign keys `<entity>_id`; timestamps `<verb>_at` | `project_id`, `consumed_at` |
| React components | `PascalCase.tsx` | `BoardGrid.tsx`, `StatusCell.tsx` |
| Hooks | `useCamelCase.ts` | `useUpdateCell.ts` |
| Non-component TS | `camelCase.ts` | `offlineQueue.ts` |
| i18n keys | `namespace:section.key` | `board:filters.clearAll` |
| Permission strings | `resource:action[:qualifier]` | `task:update:status` |
| API routes | plural nouns, kebab-case paths | `/api/v1/projects/{id}/columns` |
| WebSocket events | `entity.past_tense` | `cell.changed` |
| Env vars | `SCREAMING_SNAKE_CASE` | `SENDGRID_TEMPLATE_INVITATION` |
| Branches | `type/short-description` | `feat/column-engine` |
| Commits | Conventional Commits | `feat(tasks): add fractional index reordering` |

### Enforced boundaries (`.importlinter`)

```
core          →  imports nothing from modules/ or integrations/
models        →  imports only core
schemas       →  imports only core and models
modules/X     →  may import core, models, schemas, integrations
              →  may import modules/Y/service.py
              →  MUST NOT import modules/Y/router.py
integrations  →  imports only core
workers       →  may import anything (it is the outermost layer)
```

Violations fail CI. Conventions that are not mechanically enforced decay within a month.

### File-size guidance

A file past ~400 lines is a signal to split — usually into the pattern already used in `modules/tasks/` (`service.py` for orchestration, focused modules for the hard parts). Not a hard limit; a prompt to look.

### Where new things go

| Adding… | Goes in |
|---|---|
| A new column type | `modules/projects/columns.py` (validation) + `components/board/cells/` (editor) + register in `cells/index.ts` |
| A new notification trigger | `modules/notifications/service.py` (event) + `templates/<event>/` (he + en copy) |
| A new permission | `core/permissions.py` registry + seed migration + `admin/RoleMatrix` |
| A new external provider | `integrations/` only — never imported directly by a module |
| A new scheduled job | `workers/tasks_*.py` + `workers/beat_schedule.py` |
| A new report | `modules/reports/` + `features/reports/` |
| A shared UI primitive | `components/ui/` (if generic) or `components/common/` (if app-specific) |