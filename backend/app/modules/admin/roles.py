"""The role → permission matrix (FR-203).

This is the most dangerous screen in the application: it is the one place where a
single request changes what many people may do. Three consequences show up in the
code below — the write is a delta rather than a rebuild, it refuses to leave
nobody able to undo it, and it flushes the whole permission cache.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RoleKey, UserStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.permissions import PERMISSION_KEYS
from app.models.role import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.schemas.admin import PermissionRow, RoleRow

# Losing this one is unrecoverable through the UI: nobody left can grant it back.
GRANT_PERMISSION = "user:manage_permissions"


async def list_permissions(db: AsyncSession) -> list[PermissionRow]:
    """Read from the table, not from `core.permissions.PERMISSIONS`.

    The registry is the source the seeder works from, but `role_permissions`
    references *rows*. A permission added to the registry and not yet seeded
    cannot actually be granted, and a matrix screen that offered it would produce
    a save that fails. This lists what is grantable.
    """
    rows = await db.scalars(select(Permission).order_by(Permission.resource, Permission.key))
    return [
        PermissionRow(
            key=row.key,
            resource=row.resource,
            description_he=row.description_he,
            description_en=row.description_en,
        )
        for row in rows
    ]


async def list_roles(db: AsyncSession) -> list[RoleRow]:
    """Every role with its permissions and how many people hold it.

    Three small queries rather than one join with two aggregates: at five roles
    and fifty users the cost is noise, and each is legible on its own.
    """
    roles = list(await db.scalars(select(Role).order_by(Role.rank)))

    granted: dict[uuid.UUID, list[str]] = {}
    for role_id, permission_key in await db.execute(
        select(RolePermission.role_id, Permission.key).join(
            Permission, Permission.id == RolePermission.permission_id
        )
    ):
        granted.setdefault(role_id, []).append(permission_key)

    counts: dict[uuid.UUID, int] = {}
    for role_id, count in await db.execute(
        select(UserRole.role_id, func.count()).group_by(UserRole.role_id)
    ):
        counts[role_id] = count

    return [
        RoleRow(
            id=str(role.id),
            key=role.key,
            label_he=role.label_he,
            label_en=role.label_en,
            rank=role.rank,
            is_system=role.is_system,
            permission_keys=sorted(granted.get(role.id, [])),
            user_count=int(counts.get(role.id, 0)),
        )
        for role in roles
    ]


async def load_role(db: AsyncSession, role_id: uuid.UUID) -> Role:
    role = await db.get(Role, role_id)
    if role is None:
        raise NotFoundError("That role does not exist.")
    return role


async def _current_keys(db: AsyncSession, role_id: uuid.UUID) -> set[str]:
    rows = await db.scalars(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return {str(key) for key in rows}


async def _active_grant_holders_excluding(db: AsyncSession, role_id: uuid.UUID) -> int:
    """How many *active* users would still hold `user:manage_permissions` if this
    role lost it."""
    count = await db.scalar(
        select(func.count(func.distinct(UserRole.user_id)))
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .join(User, User.id == UserRole.user_id)
        .where(
            Permission.key == GRANT_PERMISSION,
            UserRole.role_id != role_id,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
    )
    return int(count or 0)


async def replace_role_permissions(
    db: AsyncSession, role: Role, wanted_keys: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Set a role's permissions to exactly `wanted_keys`. Returns the audit diff.

    Applied as a delta — delete what left, insert what arrived — rather than
    delete-all-then-reinsert. Rebuilding would churn every row the edit did not
    touch, and it would make the audit `before`/`after` describe a change to
    everything.
    """
    wanted = set(wanted_keys)

    unknown = sorted(wanted - PERMISSION_KEYS)
    if unknown:
        raise ValidationError(
            "Unknown permission(s).",
            errors=[{"field": "permission_keys", "message": key} for key in unknown],
        )

    current = await _current_keys(db, role.id)

    # The self-lockout guard. Without it one PUT can leave the installation with
    # nobody able to edit permissions, and the only way back is a database shell
    # or a re-seed. A 422 is a much better outcome than either.
    losing_grant = GRANT_PERMISSION in current and GRANT_PERMISSION not in wanted
    if losing_grant and await _active_grant_holders_excluding(db, role.id) == 0:
        raise ConflictError(
            "This is the last role that can manage permissions. Grant "
            "'user:manage_permissions' to another role first."
        )

    added, removed = sorted(wanted - current), sorted(current - wanted)
    if not added and not removed:
        return {}, {}

    if removed:
        doomed = select(Permission.id).where(Permission.key.in_(removed))
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id.in_(doomed),
            )
        )

    if added:
        rows = await db.execute(
            select(Permission.id, Permission.key).where(Permission.key.in_(added))
        )
        found = {key: permission_id for permission_id, key in rows}
        missing = sorted(set(added) - set(found))
        if missing:
            # In the registry but not seeded — see `list_permissions`.
            raise ValidationError(
                "Permission(s) not present in this database. Run the seed.",
                errors=[{"field": "permission_keys", "message": key} for key in missing],
            )
        for key in added:
            db.add(RolePermission(role_id=role.id, permission_id=found[key]))

    await db.flush()

    return (
        {"permission_keys": sorted(current)},
        {"permission_keys": sorted(wanted), "added": added, "removed": removed},
    )


async def role_by_key(db: AsyncSession, key: RoleKey) -> Role:
    role = await db.scalar(select(Role).where(Role.key == key))
    if role is None:
        raise NotFoundError(f"Role {key.value} is not seeded.")
    return role
