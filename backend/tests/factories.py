"""Model builders for tests.

Plain async helpers rather than factory-boy classes: factory-boy's async support
requires a session bound at class-definition time, which fights the
transaction-per-test fixture. These are simpler and read better at call sites.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    ColumnType,
    InvitationStatus,
    ProjectPermissionLevel,
    RoleKey,
    UserStatus,
)
from app.core.security import generate_token, hash_password, hash_token
from app.core.time import local_today, utc_now
from app.models import (
    BoardColumn,
    Group,
    Invitation,
    Line,
    Project,
    ProjectMember,
    Role,
    Site,
    Task,
    User,
    UserRole,
)

TEST_PASSWORD = "correct-horse-battery-staple"
# Hashed once per session — argon2 is intentionally slow, so hashing per user
# would dominate the suite's runtime.
_PASSWORD_HASH: str | None = None


def _password_hash() -> str:
    global _PASSWORD_HASH
    if _PASSWORD_HASH is None:
        _PASSWORD_HASH = hash_password(TEST_PASSWORD)
    return _PASSWORD_HASH


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@kavim.test"


async def make_user(
    db: AsyncSession,
    *,
    email: str | None = None,
    full_name: str = "Test User",
    role: RoleKey | None = None,
    status: UserStatus = UserStatus.ACTIVE,
    phone: str | None = None,
    phone_verified: bool = False,
) -> User:
    user = User(
        email=email or unique_email(),
        full_name=full_name,
        status=status,
        password_hash=_password_hash(),
        phone=phone,
        phone_verified_at=utc_now() if phone and phone_verified else None,
    )
    db.add(user)
    await db.flush()

    if role is not None:
        role_row = await db.scalar(select(Role).where(Role.key == role))
        if role_row is None:
            raise LookupError(f"role {role} not seeded — request the `seeded_reference` fixture")
        db.add(UserRole(user_id=user.id, role_id=role_row.id))
        await db.flush()

    return user


async def make_site_and_line(db: AsyncSession) -> tuple[Site, Line]:
    suffix = uuid.uuid4().hex[:6]
    site = Site(name=f"Site {suffix}", code=f"S-{suffix}")
    db.add(site)
    await db.flush()
    line = Line(site_id=site.id, name=f"Line {suffix}", code=f"L-{suffix}")
    db.add(line)
    await db.flush()
    return site, line


async def make_project(
    db: AsyncSession,
    *,
    created_by: User,
    name: str = "Test review",
    line: Line | None = None,
    with_status_column: bool = True,
) -> Project:
    project = Project(
        line_id=line.id if line else None,
        name=name,
        created_by=created_by.id,
        start_date=local_today(),
        end_date=local_today() + timedelta(days=7),
    )
    db.add(project)
    await db.flush()

    db.add(
        ProjectMember(
            project_id=project.id,
            user_id=created_by.id,
            permission_level=ProjectPermissionLevel.OWNER,
        )
    )

    if with_status_column:
        await make_status_column(db, project=project)

    await db.flush()
    return project


async def make_status_column(
    db: AsyncSession,
    *,
    project: Project,
    editable_by_roles: list[str] | None = None,
) -> BoardColumn:
    column = BoardColumn(
        project_id=project.id,
        key="status",
        type=ColumnType.STATUS,
        label_he="סטטוס",
        label_en="Status",
        system_field="status_key",
        position=1000,
        editable_by_roles=editable_by_roles or [RoleKey.WORKER.value],
        settings={
            "options": [
                {"key": "open", "label": {"he": "פתוח", "en": "Open"}, "is_done": False},
                {"key": "done", "label": {"he": "הושלם", "en": "Done"}, "is_done": True},
            ]
        },
    )
    db.add(column)
    await db.flush()
    return column


async def make_column(
    db: AsyncSession,
    *,
    project: Project,
    key: str,
    column_type: ColumnType = ColumnType.TEXT,
    position: float = 2000,
    editable_by_roles: list[str] | None = None,
    settings: dict[str, Any] | None = None,
) -> BoardColumn:
    column = BoardColumn(
        project_id=project.id,
        key=key,
        type=column_type,
        label_he=key,
        label_en=key,
        position=position,
        editable_by_roles=editable_by_roles or [],
        settings=settings or {},
    )
    db.add(column)
    await db.flush()
    return column


async def make_group(
    db: AsyncSession, *, project: Project, name: str = "Group", position: float = 1000
) -> Group:
    group = Group(project_id=project.id, name=name, position=position)
    db.add(group)
    await db.flush()
    return group


async def make_task(
    db: AsyncSession,
    *,
    project: Project,
    created_by: User,
    name: str = "Test task",
    group: Group | None = None,
    parent: Task | None = None,
    status_key: str | None = "open",
    owner: User | None = None,
    start_date: date | None = None,
    due_date: date | None = None,
    position: float = 1000,
    custom: dict[str, Any] | None = None,
) -> Task:
    task = Task(
        project_id=project.id,
        group_id=group.id if group else None,
        parent_task_id=parent.id if parent else None,
        name=name,
        status_key=status_key,
        owner_id=owner.id if owner else None,
        start_date=start_date,
        due_date=due_date,
        position=position,
        custom=custom or {},
        created_by=created_by.id,
    )
    db.add(task)
    await db.flush()
    return task


async def make_invitation(
    db: AsyncSession,
    *,
    invited_by: User,
    role: RoleKey = RoleKey.WORKER,
    email: str | None = None,
    expires_in_days: int = 7,
    status: InvitationStatus = InvitationStatus.PENDING,
) -> tuple[Invitation, str]:
    """Returns the row and the **raw token**.

    Only the hash is stored, so a test that needs to redeem the invitation has to
    receive the plaintext here — there is no way to recover it later.
    """
    role_row = await db.scalar(select(Role).where(Role.key == role))
    if role_row is None:
        raise LookupError(f"role {role} not seeded — request the `seeded_reference` fixture")

    raw_token = generate_token()
    invitation = Invitation(
        email=email or unique_email("invitee"),
        token_hash=hash_token(raw_token),
        role_id=role_row.id,
        invited_by=invited_by.id,
        status=status,
        expires_at=utc_now() + timedelta(days=expires_in_days),
    )
    db.add(invitation)
    await db.flush()
    return invitation, raw_token
