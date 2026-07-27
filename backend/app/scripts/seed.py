"""Seed the database with reference data and a realistic demo board.

    uv run python -m app.scripts.seed              # reference data + demo
    uv run python -m app.scripts.seed --reference  # reference data only
    uv run python -m app.scripts.seed --reset      # wipe demo data first

**Idempotent.** Re-running upserts reference data (roles, permissions) and skips
the demo project if it already exists, so it is safe to run against a database
that has been partly seeded — which is what happens in practice.

The demo board is deliberately realistic rather than minimal: 40-odd tasks with
subtasks, custom columns, overdue items, blocked items, and comments. Every UI
state the frontend needs to render is reachable without typing data in by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import dispose_engine, get_sessionmaker
from app.core.enums import (
    ColumnType,
    Locale,
    ProjectPermissionLevel,
    ProjectStatus,
    RoleKey,
    UserStatus,
)
from app.core.logging import configure_logging, get_logger
from app.core.permissions import (
    DEFAULT_ROLE_MATRIX,
    PERMISSIONS,
    ROLE_LABELS,
    ROLE_RANKS,
)
from app.core.security import hash_password
from app.core.time import local_today, utc_now
from app.models import (
    BoardColumn,
    Comment,
    Group,
    Line,
    Permission,
    Project,
    ProjectMember,
    Role,
    RolePermission,
    Site,
    Task,
    TaskAssignee,
    User,
    UserRole,
)

logger = get_logger(__name__)

DEMO_PASSWORD = "KavimDemo2026!"  # noqa: S105 - demo credential, printed on purpose
DEMO_PROJECT_NAME = "קו 3 — ביקורת היגיינה שבועית"
DEMO_SITE_CODE = "DEMO-SITE"

# Deterministic so re-seeding produces the same board and screenshots/tests stay
# comparable across runs.
RNG_SEED = 20260726


# ══════════════════════════════════════════════════════════════════════════
#  reference data
# ══════════════════════════════════════════════════════════════════════════
async def seed_permissions(db: AsyncSession) -> dict[str, Permission]:
    existing = {p.key: p for p in (await db.scalars(select(Permission))).all()}

    for spec in PERMISSIONS:
        row = existing.get(spec.key)
        if row is None:
            row = Permission(
                key=spec.key,
                resource=spec.resource,
                description_he=spec.description_he,
                description_en=spec.description_en,
            )
            db.add(row)
            existing[spec.key] = row
        else:
            # Descriptions may be reworded between releases; keep them current.
            row.resource = spec.resource
            row.description_he = spec.description_he
            row.description_en = spec.description_en

    await db.flush()
    logger.info("seeded_permissions", count=len(existing))
    return existing


async def seed_roles(db: AsyncSession, permissions: dict[str, Permission]) -> dict[RoleKey, Role]:
    existing = {r.key: r for r in (await db.scalars(select(Role))).all()}

    for key in RoleKey:
        label_he, label_en = ROLE_LABELS[key]
        row = existing.get(key)
        if row is None:
            row = Role(
                key=key,
                label_he=label_he,
                label_en=label_en,
                rank=ROLE_RANKS[key],
                is_system=True,
            )
            db.add(row)
            existing[key] = row
        else:
            row.label_he, row.label_en = label_he, label_en
            row.rank = ROLE_RANKS[key]

    await db.flush()

    # Top the matrix back up to the seeded default. Note carefully: this only
    # *adds* missing rows. It never computes `current - wanted` and never
    # deletes, so it is a **one-way ratchet toward the defaults**:
    #
    #   admin revokes a default permission  →  silently restored by a re-seed
    #   admin grants a non-default one      →  survives a re-seed untouched
    #
    # That asymmetry is deliberate and is load-bearing in both directions.
    #
    # Good: `DEFAULT_ROLE_MATRIX[SYSTEM_ADMIN]` is every permission, so
    # `seed --reference` is the documented way back from a SYSTEM_ADMIN whose
    # permissions were stripped. It is what makes the lockout guard in
    # `modules/admin/roles.py` a convenience rather than the only thing standing
    # between an operator and a database shell.
    #
    # Bad: a deploy that runs `seed --reference` will quietly undo a deliberate
    # revocation. Check the deployment scripts before relying on FR-203 in
    # production.
    #
    # Making this reconcile-and-delete instead would destroy every runtime matrix
    # edit on every deploy, which is strictly worse. Two tests in
    # `tests/integration/test_seed_matrix_interaction.py` pin both directions so
    # this stays a decision rather than becoming a surprise.
    for key, role in existing.items():
        wanted = DEFAULT_ROLE_MATRIX[key]
        current = set(
            (
                await db.scalars(
                    select(Permission.key)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id == role.id)
                )
            ).all()
        )
        for permission_key in wanted - current:
            db.add(RolePermission(role_id=role.id, permission_id=permissions[permission_key].id))

    await db.flush()
    logger.info("seeded_roles", count=len(existing))
    return existing


# ══════════════════════════════════════════════════════════════════════════
#  demo data
# ══════════════════════════════════════════════════════════════════════════
DEMO_USERS: tuple[tuple[str, str, RoleKey, str | None], ...] = (
    ("admin@kavim.example.com", "אלעד מנהל מערכת", RoleKey.SYSTEM_ADMIN, None),
    ("manager@kavim.example.com", "רונית מנהלת קו", RoleKey.LINE_MANAGER, "+972501000001"),
    ("supervisor@kavim.example.com", "יוסי אחראי משמרת", RoleKey.SHIFT_SUPERVISOR, "+972501000002"),
    ("worker1@kavim.example.com", "מאיה עובדת קו", RoleKey.WORKER, "+972501000003"),
    ("worker2@kavim.example.com", "דוד עובד קו", RoleKey.WORKER, "+972501000004"),
    ("worker3@kavim.example.com", "נועה עובדת קו", RoleKey.WORKER, None),
    ("auditor@kavim.example.com", "תמר מבקרת איכות", RoleKey.VIEWER, None),
)


async def seed_users(db: AsyncSession, roles: dict[RoleKey, Role]) -> dict[str, User]:
    existing = {u.email.lower(): u for u in (await db.scalars(select(User))).all()}
    password_hash = hash_password(DEMO_PASSWORD)
    now = utc_now()

    for email, full_name, role_key, phone in DEMO_USERS:
        if email in existing:
            continue
        user = User(
            email=email,
            full_name=full_name,
            phone=phone,
            # Verified so SMS paths are exercisable; unverified users exist too
            # (worker3, auditor) so the "cannot receive SMS" branch is reachable.
            phone_verified_at=now if phone else None,
            locale=Locale.HE,
            status=UserStatus.ACTIVE,
            password_hash=password_hash,
            password_changed_at=now,
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[role_key].id))
        existing[email] = user

    await db.flush()
    logger.info("seeded_users", count=len(existing))
    return existing


async def seed_site(db: AsyncSession) -> Line:
    site = await db.scalar(select(Site).where(Site.code == DEMO_SITE_CODE))
    if site is None:
        site = Site(name="מפעל הדגמה", code=DEMO_SITE_CODE, timezone=settings.DEFAULT_TIMEZONE)
        db.add(site)
        await db.flush()

    line = await db.scalar(select(Line).where(Line.site_id == site.id, Line.code == "L3"))
    if line is None:
        line = Line(site_id=site.id, name="קו 3 — אריזה", code="L3")
        db.add(line)
        await db.flush()

    return line


# Column definitions for the demo board. The first five are *system* columns —
# backed by real columns on `tasks` — and cannot be deleted, only relabelled.
DEMO_COLUMNS: tuple[dict[str, Any], ...] = (
    {
        "key": "status",
        "type": ColumnType.STATUS,
        "label_he": "סטטוס",
        "label_en": "Status",
        "system_field": "status_key",
        "width": 150,
        # Workers own status. This single line is what FR-205 is about.
        "editable_by_roles": [RoleKey.WORKER, RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {
            "options": [
                {
                    "key": "open",
                    "label": {"he": "לא התחיל", "en": "Not started"},
                    "color": "#94a3b8",
                    "is_done": False,
                },
                {
                    "key": "in_progress",
                    "label": {"he": "בביצוע", "en": "In progress"},
                    "color": "#3b82f6",
                    "is_done": False,
                },
                {
                    "key": "blocked",
                    "label": {"he": "תקוע", "en": "Blocked"},
                    "color": "#ef4444",
                    "is_done": False,
                },
                {
                    "key": "review",
                    "label": {"he": "בבדיקה", "en": "In review"},
                    "color": "#a855f7",
                    "is_done": False,
                },
                {
                    "key": "done",
                    "label": {"he": "הושלם", "en": "Done"},
                    "color": "#22c55e",
                    "is_done": True,
                },
            ]
        },
    },
    {
        "key": "owner",
        "type": ColumnType.PERSON,
        "label_he": "אחראי",
        "label_en": "Owner",
        "system_field": "owner_id",
        "width": 130,
        "editable_by_roles": [RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {},
    },
    {
        "key": "start_date",
        "type": ColumnType.DATE,
        "label_he": "תאריך התחלה",
        "label_en": "Start date",
        "system_field": "start_date",
        "width": 130,
        "editable_by_roles": [RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {},
    },
    {
        "key": "due_date",
        "type": ColumnType.DATE,
        "label_he": "תאריך יעד",
        "label_en": "Due date",
        "system_field": "due_date",
        "width": 130,
        # Workers may move their own due date; the audit log records who did.
        "editable_by_roles": [RoleKey.WORKER, RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {},
    },
    {
        "key": "priority",
        "type": ColumnType.RATING,
        "label_he": "עדיפות",
        "label_en": "Priority",
        "system_field": "priority",
        "width": 110,
        "editable_by_roles": [RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {"max": 4},
    },
    # ── user-defined columns, stored in tasks.custom ───────────────────────
    {
        "key": "severity",
        "type": ColumnType.DROPDOWN,
        "label_he": "חומרת סטייה",
        "label_en": "Deviation severity",
        "width": 150,
        "editable_by_roles": [RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {
            "options": [
                {"key": "minor", "label": {"he": "קלה", "en": "Minor"}, "color": "#22c55e"},
                {"key": "major", "label": {"he": "חמורה", "en": "Major"}, "color": "#f59e0b"},
                {
                    "key": "critical",
                    "label": {"he": "קריטית", "en": "Critical"},
                    "color": "#ef4444",
                },
            ]
        },
    },
    {
        "key": "station",
        "type": ColumnType.TEXT,
        "label_he": "עמדה",
        "label_en": "Station",
        "width": 140,
        "editable_by_roles": [RoleKey.WORKER, RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {"max_length": 100},
    },
    {
        "key": "measured_temp",
        "type": ColumnType.NUMBER,
        "label_he": "טמפרטורה נמדדת",
        "label_en": "Measured temperature",
        "width": 150,
        "editable_by_roles": [RoleKey.WORKER, RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {"unit": "°C", "min": -30, "max": 120, "precision": 1},
    },
    {
        "key": "corrective_action",
        "type": ColumnType.LONG_TEXT,
        "label_he": "פעולה מתקנת",
        "label_en": "Corrective action",
        "width": 240,
        "editable_by_roles": [RoleKey.WORKER, RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {},
    },
    {
        "key": "verified",
        "type": ColumnType.CHECKBOX,
        "label_he": "אומת",
        "label_en": "Verified",
        "width": 100,
        # Manager-only: a worker must not be able to sign off their own fix.
        # This is the asymmetry the column-permission layer exists for.
        "editable_by_roles": [RoleKey.LINE_MANAGER],
        "settings": {},
    },
    {
        "key": "root_cause_approved_by",
        "type": ColumnType.PERSON,
        "label_he": "אישור סיבת שורש",
        "label_en": "Root cause approved by",
        "width": 170,
        "editable_by_roles": [RoleKey.LINE_MANAGER],
        "settings": {},
    },
    {
        "key": "evidence",
        "type": ColumnType.FILE,
        "label_he": "תיעוד",
        "label_en": "Evidence",
        "width": 130,
        "editable_by_roles": [RoleKey.WORKER, RoleKey.SHIFT_SUPERVISOR, RoleKey.LINE_MANAGER],
        "settings": {"max_files": 10},
    },
)

DEMO_GROUPS: tuple[tuple[str, str], ...] = (
    ("משמרת א׳ — בוקר", "#0f766e"),
    ("משמרת ב׳ — צהריים", "#3b82f6"),
    ("סטיות פתוחות", "#ef4444"),
)

# (group index, task name, [subtask names])
DEMO_TASKS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (0, "בדיקת ניקיון מסוע ראשי", ("פירוק מגני מסוע", "שטיפה בחומר מאושר", "ייבוש ואישור חוזר")),
    (0, "מדידת טמפרטורת מקררים", ("מקרר 1", "מקרר 2", "מקרר 3")),
    (0, "בדיקת תוקף חומרי ניקוי", ()),
    (0, "אימות כיול מדי לחץ", ("עמדה 1", "עמדה 2")),
    (0, "בדיקת שלמות אריזות דגימה", ()),
    (0, "תיעוד לבוש והיגיינה אישית", ()),
    (0, "בדיקת מלכודות מזיקים", ("אזור קבלה", "אזור ייצור", "אזור מחסן")),
    (0, "ניקוי מסנני אוויר", ()),
    (1, "בדיקת ניקיון עמדות מילוי", ("עמדה 4", "עמדה 5")),
    (1, "מדידת ריכוז כלור במים", ()),
    (1, "בדיקת תקינות גלאי מתכות", ("כיול עם דגימת ברזל", "כיול עם דגימת נירוסטה")),
    (1, "אימות משקל ממוצע אריזה", ()),
    (1, "בדיקת סגירת מכסים", ()),
    (1, "ניקוי רצפות ותעלות ניקוז", ()),
    (1, "בדיקת תאריכי הדפסה", ()),
    (2, "סטייה: שאריות חומר על מסוע 2", ("זיהוי סיבת שורש", "ביצוע פעולה מתקנת", "אימות מנהל")),
    (2, "סטייה: טמפרטורת מקרר 2 מעל הטווח", ("בדיקת מדחס", "החלפת חוגה", "מדידה חוזרת")),
    (2, "סטייה: גלאי מתכות לא זיהה דגימה", ("בדיקת רגישות", "כיול מחדש")),
    (2, "סטייה: אריזה פגומה בעמדה 5", ()),
    (2, "סטייה: חוסר תיעוד ניקוי ממשמרת קודמת", ()),
)

DEMO_COMMENTS: tuple[str, ...] = (
    "בוצע לפי הנוהל. צילום מצורף.",
    "נדרשת בדיקה חוזרת במשמרת הבאה.",
    "החלפתי את החוגה, המדידה חזרה לטווח.",
    "לא הצלחתי להשלים — חסר חומר ניקוי מאושר במחסן.",
    "מאשרת. סיבת השורש טופלה.",
    "העברתי לאחראי משמרת להמשך טיפול.",
)


async def seed_demo_project(db: AsyncSession, line: Line, users: dict[str, User]) -> Project | None:
    existing = await db.scalar(select(Project).where(Project.name == DEMO_PROJECT_NAME))
    if existing is not None:
        logger.info("demo_project_exists", project_id=str(existing.id))
        return None

    # Not cryptographic — deterministic demo data, seeded so re-running produces
    # the same board and screenshots stay comparable.
    rng = random.Random(RNG_SEED)  # noqa: S311
    today = local_today()
    manager = users["manager@kavim.example.com"]
    supervisor = users["supervisor@kavim.example.com"]
    workers = [users[f"worker{n}@kavim.example.com"] for n in (1, 2, 3)]
    auditor = users["auditor@kavim.example.com"]

    project = Project(
        line_id=line.id,
        name=DEMO_PROJECT_NAME,
        description="ביקורת היגיינה שבועית לקו 3, כולל סטיות ופעולות מתקנות.",
        status=ProjectStatus.ACTIVE,
        start_date=today - timedelta(days=3),
        end_date=today + timedelta(days=4),
        created_by=manager.id,
    )
    db.add(project)
    await db.flush()

    # ── membership ────────────────────────────────────────────────────────
    db.add_all(
        [
            ProjectMember(
                project_id=project.id,
                user_id=manager.id,
                permission_level=ProjectPermissionLevel.OWNER,
                added_by=manager.id,
            ),
            ProjectMember(
                project_id=project.id,
                user_id=supervisor.id,
                permission_level=ProjectPermissionLevel.EDITOR,
                added_by=manager.id,
            ),
            ProjectMember(
                project_id=project.id,
                user_id=auditor.id,
                permission_level=ProjectPermissionLevel.VIEWER,
                added_by=manager.id,
            ),
            *[
                ProjectMember(
                    project_id=project.id,
                    user_id=worker.id,
                    permission_level=ProjectPermissionLevel.COMMENTER,
                    added_by=manager.id,
                )
                for worker in workers
            ],
        ]
    )

    # ── columns ───────────────────────────────────────────────────────────
    for index, spec in enumerate(DEMO_COLUMNS):
        db.add(
            BoardColumn(
                project_id=project.id,
                key=spec["key"],
                type=spec["type"],
                label_he=spec["label_he"],
                label_en=spec["label_en"],
                settings=spec["settings"],
                # Gaps of 1000 leave room for many inserts before a rebalance.
                position=(index + 1) * 1000,
                width=spec["width"],
                editable_by_roles=[str(r) for r in spec["editable_by_roles"]],
                system_field=spec.get("system_field"),
            )
        )

    # ── groups ────────────────────────────────────────────────────────────
    groups: list[Group] = []
    for index, (name, color) in enumerate(DEMO_GROUPS):
        group = Group(project_id=project.id, name=name, color=color, position=(index + 1) * 1000)
        db.add(group)
        groups.append(group)
    await db.flush()

    # ── tasks ─────────────────────────────────────────────────────────────
    statuses = ["open", "in_progress", "blocked", "review", "done"]
    severities = ["minor", "major", "critical"]
    stations = ["עמדה 1", "עמדה 2", "עמדה 3", "עמדה 4", "עמדה 5", "אזור קבלה"]

    task_count = 0
    for group_index, task_name, subtask_names in DEMO_TASKS:
        group = groups[group_index]
        status = rng.choice(statuses)
        assignee = rng.choice(workers)
        # A spread of due dates so overdue, due-today, and future all appear.
        due_offset = rng.choice([-4, -2, -1, 0, 1, 2, 3, 5, 7])
        due = today + timedelta(days=due_offset)
        # Derive start from due, never independently — the `due_date >= start_date`
        # constraint is real, and an overdue task must have started even earlier.
        start = min(today - timedelta(days=rng.randint(0, 3)), due)
        is_deviation = group_index == 2

        task = Task(
            project_id=project.id,
            group_id=group.id,
            name=task_name,
            status_key=status,
            owner_id=assignee.id,
            start_date=start,
            due_date=due,
            priority=rng.randint(1, 4) if is_deviation else rng.randint(0, 2),
            position=(task_count + 1) * 1000,
            custom={
                "station": rng.choice(stations),
                "severity": rng.choice(severities) if is_deviation else "minor",
                "measured_temp": round(rng.uniform(1.0, 8.0), 1),
                "verified": status == "done" and rng.random() > 0.4,
                "corrective_action": ("בוצעה שטיפה חוזרת ואומת ניקיון." if is_deviation else None),
            },
            completed_at=utc_now() if status == "done" else None,
            created_by=supervisor.id,
        )
        db.add(task)
        await db.flush()
        task_count += 1

        db.add(TaskAssignee(task_id=task.id, user_id=assignee.id, assigned_by=supervisor.id))

        # Subtasks inherit the parent's group; depth is capped at 2.
        for sub_index, subtask_name in enumerate(subtask_names):
            sub_status = rng.choice(statuses)
            sub_assignee = rng.choice(workers)
            sub_due = today + timedelta(days=rng.randint(-2, 5))
            subtask = Task(
                project_id=project.id,
                group_id=group.id,
                parent_task_id=task.id,
                name=subtask_name,
                status_key=sub_status,
                owner_id=sub_assignee.id,
                start_date=min(start, sub_due),
                due_date=sub_due,
                priority=rng.randint(0, 2),
                position=(sub_index + 1) * 1000,
                custom={"station": rng.choice(stations)},
                completed_at=utc_now() if sub_status == "done" else None,
                created_by=supervisor.id,
            )
            db.add(subtask)
            await db.flush()
            task_count += 1
            db.add(
                TaskAssignee(task_id=subtask.id, user_id=sub_assignee.id, assigned_by=supervisor.id)
            )

        # Comments on roughly half the tasks.
        if rng.random() > 0.5:
            author = rng.choice([*workers, supervisor])
            db.add(
                Comment(
                    task_id=task.id,
                    author_id=author.id,
                    body=rng.choice(DEMO_COMMENTS),
                )
            )

    await db.flush()
    logger.info("seeded_demo_project", project_id=str(project.id), tasks=task_count)
    return project


async def reset_demo_data(db: AsyncSession) -> None:
    """Delete the demo project and site. Reference data is left in place."""
    project = await db.scalar(select(Project).where(Project.name == DEMO_PROJECT_NAME))
    if project is not None:
        # Cascades handle groups, columns, tasks, assignees, comments, members.
        await db.execute(delete(Project).where(Project.id == project.id))
        logger.info("deleted_demo_project")

    site = await db.scalar(select(Site).where(Site.code == DEMO_SITE_CODE))
    if site is not None:
        await db.execute(delete(Site).where(Site.id == site.id))
        logger.info("deleted_demo_site")

    emails = [email for email, _, _, _ in DEMO_USERS]
    await db.execute(delete(User).where(User.email.in_(emails)))
    logger.info("deleted_demo_users", count=len(emails))
    await db.flush()


# ══════════════════════════════════════════════════════════════════════════
#  entrypoint
# ══════════════════════════════════════════════════════════════════════════
async def run(*, reference_only: bool, reset: bool) -> None:
    # The engine must be disposed inside this same event loop. Disposing from a
    # second asyncio.run() raises "'NoneType' object has no attribute 'send'",
    # because asyncpg's connections belong to the loop that created them.
    try:
        async with get_sessionmaker()() as db:
            if reset:
                await reset_demo_data(db)

            permissions = await seed_permissions(db)
            roles = await seed_roles(db, permissions)

            if not reference_only:
                users = await seed_users(db, roles)
                line = await seed_site(db)
                await seed_demo_project(db, line, users)

            await db.commit()
    finally:
        await dispose_engine()

    if not reference_only:
        print("\n" + "─" * 68)  # noqa: T201
        print("  Demo accounts — password for all:  " + DEMO_PASSWORD)  # noqa: T201
        print("─" * 68)  # noqa: T201
        for email, full_name, role_key, _ in DEMO_USERS:
            print(f"  {email:<28} {role_key.value:<18} {full_name}")  # noqa: T201
        print("─" * 68 + "\n")  # noqa: T201


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Kavim database.")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="seed roles and permissions only, no demo data (use for production)",
    )
    parser.add_argument("--reset", action="store_true", help="delete existing demo data first")
    args = parser.parse_args(argv)

    # The seed logs Hebrew project and task names. A Windows console defaults to
    # cp1252, which cannot encode them, so the run dies with UnicodeEncodeError
    # partway through — after writing rows, which makes it look like a data bug.
    # Reconfiguring the streams keeps the documented command working as written
    # instead of requiring PYTHONIOENCODING to be set first.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    configure_logging()

    if settings.is_production and not args.reference:
        print(  # noqa: T201
            "refusing to seed demo data in production; use --reference",
            file=sys.stderr,
        )
        return 1

    asyncio.run(run(reference_only=args.reference, reset=args.reset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
