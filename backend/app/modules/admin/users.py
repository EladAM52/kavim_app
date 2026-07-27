"""User administration (FR-201, FR-202, FR-206, FR-207, FR-210)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ProjectPermissionLevel, RoleKey, UserStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.permissions import (
    PROJECT_LEVEL_PERMISSIONS,
    column_is_editable,
    resolve_effective_permissions,
)
from app.core.time import utc_now
from app.models.column import BoardColumn
from app.models.role import Role, UserRole
from app.models.user import User
from app.modules.admin.roles import GRANT_PERMISSION, role_by_key
from app.modules.auth import authz
from app.modules.auth import service as auth_service
from app.schemas.admin import (
    AdminUserRow,
    AdminUserUpdate,
    ColumnVerdict,
    EffectivePermissionsTrace,
)
from app.schemas.common import Page, cursor_datetime, decode_cursor, encode_cursor

MAX_PAGE = 200


# ══════════════════════════════════════════════════════════════════════════
#  listing (FR-201)
# ══════════════════════════════════════════════════════════════════════════
def _apply_cursor(statement: Select[Any], cursor: str | None) -> Select[Any]:
    """Keyset, not `OFFSET`.

    SPEC §9.1 rules `OFFSET` out because it skips and repeats rows when the set
    shifts underneath a paging client — which is exactly what a user list does
    while people are being invited.

    `(created_at, id)` rather than `created_at` alone: two users created in the
    same transaction share a timestamp, and a single-column cursor would either
    lose one of them or serve it twice forever.
    """
    if cursor is None:
        return statement
    payload = decode_cursor(cursor)
    try:
        anchor = (cursor_datetime(payload, "created_at"), uuid.UUID(str(payload["id"])))
    except (KeyError, ValueError) as exc:
        raise ValidationError("That page cursor is not valid.") from exc
    return statement.where(tuple_(User.created_at, User.id) < anchor)


async def list_users(
    db: AsyncSession,
    *,
    limit: int = 50,
    cursor: str | None = None,
    query: str | None = None,
    role: RoleKey | None = None,
    status: UserStatus | None = None,
) -> Page[AdminUserRow]:
    limit = max(1, min(limit, MAX_PAGE))

    statement = select(User).where(User.deleted_at.is_(None))

    if status is not None:
        statement = statement.where(User.status == status)

    if query:
        # CITEXT already folds case on email; `ilike` covers the name.
        needle = f"%{query.strip()}%"
        statement = statement.where(or_(User.full_name.ilike(needle), User.email.ilike(needle)))

    if role is not None:
        role_row = await role_by_key(db, role)
        statement = statement.where(
            User.id.in_(select(UserRole.user_id).where(UserRole.role_id == role_row.id))
        )

    statement = _apply_cursor(statement, cursor)
    # One extra row decides whether there is a next page, without a COUNT.
    statement = statement.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)

    users = list(await db.scalars(statement))
    has_more = len(users) > limit
    users = users[:limit]

    roles_by_user = await _roles_for(db, [user.id for user in users])

    items = [
        AdminUserRow(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            status=user.status,
            locale=user.locale,
            roles=roles_by_user.get(user.id, []),
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            locked_until=user.locked_until,
        )
        for user in users
    ]

    next_cursor = None
    if has_more and users:
        last = users[-1]
        next_cursor = encode_cursor({"created_at": last.created_at, "id": str(last.id)})

    return Page[AdminUserRow](items=items, next_cursor=next_cursor)


async def _roles_for(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """One query for the whole page rather than one per row."""
    if not user_ids:
        return {}
    out: dict[uuid.UUID, list[str]] = {}
    for user_id, key in await db.execute(
        select(UserRole.user_id, Role.key)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
    ):
        out.setdefault(user_id, []).append(str(key))
    for keys in out.values():
        keys.sort()
    return out


async def load_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("That user does not exist.")
    return user


# ══════════════════════════════════════════════════════════════════════════
#  role and status changes (FR-202, FR-206)
# ══════════════════════════════════════════════════════════════════════════
async def _active_admin_count(db: AsyncSession, excluding: uuid.UUID) -> int:
    """Active users other than `excluding` who can still manage permissions."""
    from app.models.role import Permission, RolePermission

    count = await db.scalar(
        select(func.count(func.distinct(UserRole.user_id)))
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .join(User, User.id == UserRole.user_id)
        .where(
            Permission.key == GRANT_PERMISSION,
            UserRole.user_id != excluding,
            User.status == UserStatus.ACTIVE,
            User.deleted_at.is_(None),
        )
    )
    return int(count or 0)


async def apply_user_update(
    db: AsyncSession, target: User, payload: AdminUserUpdate, *, actor_id: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Change a user's role and/or status. Returns `(before, after, sessions_revoked)`.

    Three guards, all `409`, all protecting against an administrator making the
    installation unadministrable:

    * **No self-deactivation.** Locking yourself out is never the intent, and
      undoing it needs the account that just became unusable.
    * **No self-demotion.** Same problem, arrived at differently.
    * **Not the last active permission manager.** Whether by demotion or by
      deactivation, somebody must be left who can put it back.
    """
    if payload.role_key is None and payload.status is None:
        raise ValidationError("Provide a role, a status, or both.")

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    revoked = 0

    self_targeted = target.id == actor_id

    # ── status ────────────────────────────────────────────────────────────
    if payload.status is not None and payload.status != target.status:
        if self_targeted and payload.status != UserStatus.ACTIVE:
            raise ConflictError("You cannot deactivate your own account.")

        if payload.status != UserStatus.ACTIVE and await _would_orphan_admin(db, target):
            raise ConflictError("This is the last active user who can manage permissions.")

        before["status"] = target.status.value
        after["status"] = payload.status.value
        target.status = payload.status

        if payload.status != UserStatus.ACTIVE:
            # FR-206: "sessions revoked". `get_current_user` re-reads `is_active`
            # so the access token dies at once, but the refresh token would
            # otherwise stay valid for 30 days and mint new ones the moment the
            # account is reactivated.
            from app.core.enums import TokenRevokeReason

            revoked = await auth_service.revoke_all_user_tokens(
                db, target.id, TokenRevokeReason.ADMIN_FORCE
            )
            after["sessions_revoked"] = revoked

    # ── role ──────────────────────────────────────────────────────────────
    if payload.role_key is not None:
        current = await auth_service.load_role_keys(db, target.id)
        if [payload.role_key.value] != current:
            if self_targeted:
                raise ConflictError("You cannot change your own role.")

            if GRANT_PERMISSION in await auth_service.load_global_permissions(
                db, target.id
            ) and await _would_orphan_admin(db, target):
                raise ConflictError("This is the last active user who can manage permissions.")

            role_row = await role_by_key(db, payload.role_key)
            # SPEC treats a user as holding one global role. The table is
            # many-to-many so a second role costs no migration later, but the
            # admin screen assigns exactly one — replace rather than append.
            await db.execute(delete(UserRole).where(UserRole.user_id == target.id))
            db.add(UserRole(user_id=target.id, role_id=role_row.id, assigned_by=actor_id))

            before["roles"] = current
            after["roles"] = [payload.role_key.value]

    await db.flush()
    return before, after, revoked


async def _would_orphan_admin(db: AsyncSession, target: User) -> bool:
    """True if `target` is the last active holder of `user:manage_permissions`."""
    permissions = await auth_service.load_global_permissions(db, target.id)
    if GRANT_PERMISSION not in permissions:
        return False
    return await _active_admin_count(db, excluding=target.id) == 0


async def force_logout(db: AsyncSession, target: User) -> int:
    """FR-207. Revoke every refresh token the user holds."""
    from app.core.enums import TokenRevokeReason

    return await auth_service.revoke_all_user_tokens(db, target.id, TokenRevokeReason.ADMIN_FORCE)


# ══════════════════════════════════════════════════════════════════════════
#  the permission trace (FR-210)
# ══════════════════════════════════════════════════════════════════════════
async def effective_permissions_trace(
    db: AsyncSession, target: User, *, project_id: uuid.UUID | None
) -> EffectivePermissionsTrace:
    """ "Why can this person edit this?" — every layer, with its inputs.

    Resolved with `use_cache=False`. This screen exists to explain a discrepancy
    somebody has already noticed; answering it from a five-minute-old cache would
    make the diagnostic tool capable of reproducing the bug it is diagnosing.
    """
    roles = await auth_service.load_role_keys(db, target.id)
    layer1 = await authz.effective_permissions(db, target.id, use_cache=False)

    level: ProjectPermissionLevel | None = None
    layer2: frozenset[str] = frozenset()
    effective = layer1
    columns: list[ColumnVerdict] = []

    if project_id is not None:
        level = await authz.project_level(db, target.id, project_id)
        layer2 = PROJECT_LEVEL_PERMISSIONS[level] if level is not None else frozenset()
        effective = resolve_effective_permissions(layer1, level)

        board_columns = await db.scalars(
            select(BoardColumn)
            .where(BoardColumn.project_id == project_id, BoardColumn.deleted_at.is_(None))
            .order_by(BoardColumn.position)
        )
        columns = [
            ColumnVerdict(
                key=column.key,
                label_he=column.label_he,
                label_en=column.label_en,
                editable_by_roles=list(column.editable_by_roles),
                editable=column_is_editable(column.editable_by_roles, roles),
            )
            for column in board_columns
        ]

    return EffectivePermissionsTrace(
        user_id=str(target.id),
        email=target.email,
        roles=roles,
        layer1_role_permissions=sorted(layer1),
        layer2_project_level=level.value if level is not None else None,
        layer2_level_permissions=sorted(layer2),
        effective=sorted(effective),
        layer3_columns=columns,
        computed_at=utc_now(),
    )
