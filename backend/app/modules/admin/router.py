"""Administration endpoints (SPEC §6.4, §9.3).

Handlers stay thin: they declare authorization, call one function per aggregate,
write the audit row, commit, and invalidate. The rules live in the aggregate
modules next door.

Two conventions here that are not optional, both learned the hard way in Phase 2:

* **Commit before building the response.** `get_db` commits in the teardown of a
  `yield` dependency, which FastAPI runs *after* the response is sent, so a client
  acting immediately on a 200 can beat its own write.
* **Invalidate the permission cache after that commit, never before.** Doing it
  first lets a concurrent request miss the cache, read the pre-commit state, and
  repopulate the key with the stale value — which then survives the full TTL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import settings
from app.core.database import get_db
from app.core.enums import InvitationStatus, Locale, RoleKey, UserStatus
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.role import Role
from app.modules.admin import audit_log as audit_log_mod
from app.modules.admin import invitations as invitations_mod
from app.modules.admin import roles as roles_mod
from app.modules.admin import users as users_mod
from app.modules.audit import service as audit
from app.modules.auth import authz
from app.modules.auth import service as auth_service
from app.modules.auth.dependencies import (
    Principal,
    client_ip,
    require_permission,
    user_agent,
)
from app.schemas.admin import (
    AdminUserRow,
    AdminUserUpdate,
    AuditRow,
    EffectivePermissionsTrace,
    InvitationCreate,
    InvitationRow,
    PermissionRow,
    RolePermissionsUpdate,
    RoleRow,
)
from app.schemas.common import MessageResponse, Page

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Five resends per address per 15 minutes. The limit protects the *recipient* and
# the Gmail daily ceiling, not the server — so it is keyed on the invited address
# rather than on the administrator.
INVITATION_RESEND = rate_limit.Limit("invitation_resend", max_events=5, window_seconds=15 * 60)

# A freshly created invitation is the newest row, so the first page always
# contains it. Bounded rather than unbounded so a re-read can never become a
# full-table scan.
MAX_INVITATION_LOOKUP = 50

# Spelled out here rather than hidden in a helper, so the requirement reads
# directly above the handler it guards.
ManagesPermissions = Annotated[Principal, Depends(require_permission("user:manage_permissions"))]
ManagesUsers = Annotated[Principal, Depends(require_permission("user:manage"))]
InvitesUsers = Annotated[Principal, Depends(require_permission("user:invite"))]
ReadsAudit = Annotated[Principal, Depends(require_permission("audit:read"))]


def _accept_language(request: Request) -> Locale:
    """Fallback locale for an invitation email.

    The invitee has no account yet, so there is no stored preference to honour.
    When the caller does not state a language — the CLI, or any client written
    before `InvitationCreate.locale` existed — the inviting administrator's
    browser header is the only signal available, and it is usually right,
    because the two work at the same plant.

    It is only a fallback. The header describes the *sender*, and which language
    the invitee reads is something the sender knows and the browser does not.
    """
    header = (request.headers.get("Accept-Language") or "").lower()
    return Locale.EN if header.startswith("en") else Locale(settings.DEFAULT_LOCALE)


# ══════════════════════════════════════════════════════════════════════════
#  roles and the permission matrix (FR-203)
# ══════════════════════════════════════════════════════════════════════════
@router.get(
    "/permissions",
    response_model=list[PermissionRow],
    summary="Every grantable permission, for the matrix UI",
)
async def list_permissions(principal: ManagesPermissions, db: DbSession) -> list[PermissionRow]:
    return await roles_mod.list_permissions(db)


@router.get(
    "/roles",
    response_model=list[RoleRow],
    summary="Roles with their permissions and how many people hold them",
)
async def list_roles(principal: ManagesPermissions, db: DbSession) -> list[RoleRow]:
    return await roles_mod.list_roles(db)


@router.put(
    "/roles/{role_id}/permissions",
    response_model=RoleRow,
    summary="Replace a role's permissions (atomic)",
)
async def replace_role_permissions(
    role_id: uuid.UUID,
    payload: RolePermissionsUpdate,
    request: Request,
    principal: ManagesPermissions,
    db: DbSession,
) -> RoleRow:
    """FR-203. The whole set in one transaction, audited, cache flushed.

    The cache is flushed **entirely**, not per holder of this role. Enumerating
    holders would be cheaper and would also be wrong: if this transaction also
    changed a role assignment, the membership list misses somebody whichever side
    of the change it is read from. See `authz.invalidate_all`.
    """
    role = await roles_mod.load_role(db, role_id)
    before, after = await roles_mod.replace_role_permissions(db, role, payload.permission_keys)

    if after:
        await audit.write_audit(
            db,
            action=audit.ROLE_PERMISSIONS_CHANGED,
            entity_type="role",
            entity_id=role.id,
            actor_id=principal.id,
            before=before,
            after=after,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
        logger.info(
            "role_permissions_changed",
            role=str(role.key),
            added=after.get("added"),
            removed=after.get("removed"),
            actor_id=str(principal.id),
        )

    await db.commit()

    if after:
        await authz.invalidate_all()

    rows = await roles_mod.list_roles(db)
    return next(row for row in rows if row.id == str(role.id))


# ══════════════════════════════════════════════════════════════════════════
#  users (FR-201, FR-202, FR-206, FR-207, FR-210)
# ══════════════════════════════════════════════════════════════════════════
@router.get("/users", response_model=Page[AdminUserRow], summary="List, search, and filter users")
async def list_users(
    principal: ManagesUsers,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=users_mod.MAX_PAGE)] = 50,
    cursor: str | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Name or email")] = None,
    role: RoleKey | None = None,
    status: UserStatus | None = None,
) -> Page[AdminUserRow]:
    """`user:manage`, not `user:read`.

    `user:read` is held by WORKER and VIEWER because it backs the member picker.
    This response carries status, last login, and lockout state, which is a
    different thing entirely.
    """
    return await users_mod.list_users(
        db, limit=limit, cursor=cursor, query=q, role=role, status=status
    )


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserRow,
    summary="Change a user's role, or activate/deactivate them",
)
async def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    request: Request,
    principal: ManagesUsers,
    db: DbSession,
) -> AdminUserRow:
    """FR-202 and FR-206. Takes effect on the target's **next request**.

    That immediacy is the requirement, and it is why the cache is invalidated
    here rather than left to expire: a revoked permission that survives five
    minutes is a revoked permission that did not work.
    """
    target = await users_mod.load_user(db, user_id)
    before, after, revoked = await users_mod.apply_user_update(
        db, target, payload, actor_id=principal.id
    )

    if after:
        action = audit.USER_ROLE_CHANGED
        if "status" in after:
            action = (
                audit.USER_ACTIVATED
                if after["status"] == UserStatus.ACTIVE.value
                else audit.USER_DEACTIVATED
            )
        await audit.write_audit(
            db,
            action=action,
            entity_type="user",
            entity_id=target.id,
            actor_id=principal.id,
            before=before,
            after=after,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
        logger.info(
            "admin_user_updated",
            target_id=str(target.id),
            actor_id=str(principal.id),
            changed=sorted(after),
            sessions_revoked=revoked,
        )

    await db.commit()

    if after:
        await authz.invalidate_user(target.id)

    page = await users_mod.list_users(db, limit=1, query=target.email)
    return page.items[0]


@router.post(
    "/users/{user_id}/force-logout",
    response_model=MessageResponse,
    summary="Revoke every session a user holds",
)
async def force_logout(
    user_id: uuid.UUID,
    request: Request,
    principal: ManagesUsers,
    db: DbSession,
) -> MessageResponse:
    """FR-207. The account stays usable; the sessions do not.

    Distinct from deactivation on purpose — this is the answer to a lost phone,
    not to a departure.
    """
    target = await users_mod.load_user(db, user_id)
    revoked = await users_mod.force_logout(db, target)

    await audit.write_audit(
        db,
        action=audit.USER_FORCE_LOGOUT,
        entity_type="user",
        entity_id=target.id,
        actor_id=principal.id,
        after={"sessions_revoked": revoked},
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    await db.commit()
    await authz.invalidate_user(target.id)

    return MessageResponse(detail=f"Revoked {revoked} session(s).")


@router.get(
    "/users/{user_id}/effective-permissions",
    response_model=EffectivePermissionsTrace,
    summary="Why this user can do what they can do",
)
async def read_effective_permissions(
    user_id: uuid.UUID,
    principal: ManagesPermissions,
    db: DbSession,
    project_id: uuid.UUID | None = None,
) -> EffectivePermissionsTrace:
    """FR-210. Layers 1, 2, and 3, with the inputs that produced each.

    Needs `user:manage_permissions` rather than `user:manage`: it dumps a user's
    complete authorization state, which is more than user administration needs.

    Without `project_id` only layer 1 is meaningful — layers 2 and 3 are defined
    per project, so the response reports them empty rather than guessing.
    """
    target = await users_mod.load_user(db, user_id)
    return await users_mod.effective_permissions_trace(db, target, project_id=project_id)


# ══════════════════════════════════════════════════════════════════════════
#  invitations (FR-101, FR-111)
# ══════════════════════════════════════════════════════════════════════════
@router.post(
    "/invitations",
    response_model=InvitationRow,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone by email",
)
async def create_invitation(
    payload: InvitationCreate,
    request: Request,
    principal: InvitesUsers,
    db: DbSession,
) -> InvitationRow:
    """FR-101. Replaces `python -m app.scripts.invite`.

    The response deliberately omits the raw token. The token *is* the credential,
    and it exists in exactly one place — the emailed link. Returning it here would
    let anyone holding `user:invite` accept an invitation addressed to someone
    else.
    """
    await invitations_mod.ensure_no_account_exists(db, payload.email)
    project_ids = [uuid.UUID(pid) for pid in payload.project_ids]
    await invitations_mod.validate_project_ids(db, project_ids)

    role = await roles_mod.role_by_key(db, payload.role_key)
    invitation, _raw_token = await auth_service.invite_user(
        db,
        email=payload.email,
        role_id=role.id,
        invited_by=principal.id,
        project_ids=project_ids,
        locale=payload.locale or _accept_language(request),
        ip=client_ip(request),
        user_agent=user_agent(request),
    )

    # The outbox row must be committed before the 201: the sweeper runs in another
    # process and can only see committed rows.
    await db.commit()

    return await _one_invitation(db, invitation.id)


@router.get(
    "/invitations",
    response_model=Page[InvitationRow],
    summary="Pending, consumed, and revoked invitations",
)
async def list_invitations(
    principal: InvitesUsers,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=invitations_mod.MAX_PAGE)] = 50,
    cursor: str | None = None,
    status_filter: Annotated[InvitationStatus | None, Query(alias="status")] = None,
) -> Page[InvitationRow]:
    return await invitations_mod.list_invitations(
        db, limit=limit, cursor=cursor, status=status_filter
    )


@router.post(
    "/invitations/{invitation_id}/resend",
    response_model=InvitationRow,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Issue a new link and invalidate the old one",
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    principal: InvitesUsers,
    db: DbSession,
) -> InvitationRow:
    """FR-111. The previous link stops working immediately.

    `create_invitation` already supersedes the pending row for an address, so
    resending is creating — which also means the old token is revoked rather than
    left alive alongside the new one.

    Rate limited per address. Nothing else stops an administrator from mail-
    bombing an inbox, and Gmail's ~500 recipients/day is a hard ceiling that takes
    OTP delivery down with it when breached (SPEC R2).
    """
    original = await invitations_mod.load_invitation(db, invitation_id)
    invitations_mod.require_pending(original)

    await rate_limit.enforce(INVITATION_RESEND, original.email)

    role = await db.get(Role, original.role_id)
    if role is None:  # pragma: no cover - a role cannot be deleted while referenced
        raise NotFoundError("The invited role no longer exists.")

    replacement, _raw_token = await auth_service.invite_user(
        db,
        email=original.email,
        role_id=original.role_id,
        invited_by=principal.id,
        project_ids=list(original.project_ids or []),
        locale=_accept_language(request),
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    await audit.write_audit(
        db,
        action=audit.INVITATION_RESENT,
        entity_type="invitation",
        entity_id=original.id,
        actor_id=principal.id,
        after={"superseded_by": str(replacement.id), "email": original.email},
        ip=client_ip(request),
        user_agent=user_agent(request),
    )

    await db.commit()
    return await _one_invitation(db, replacement.id)


@router.delete(
    "/invitations/{invitation_id}",
    response_model=MessageResponse,
    summary="Revoke a pending invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    principal: InvitesUsers,
    db: DbSession,
) -> MessageResponse:
    """FR-111. `200` with a message rather than `204`.

    The caller gets told what happened, and the same call on an already-revoked
    invitation succeeds — a double-click must not produce an error for an outcome
    the user already has.
    """
    invitation = await invitations_mod.load_invitation(db, invitation_id)
    before = invitations_mod.revoke(invitation)

    await audit.write_audit(
        db,
        action=audit.INVITATION_REVOKED,
        entity_type="invitation",
        entity_id=invitation.id,
        actor_id=principal.id,
        before=before,
        after={"status": invitation.status.value, "email": invitation.email},
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    await db.commit()

    return MessageResponse(detail="Invitation revoked. The link no longer works.")


async def _one_invitation(db: AsyncSession, invitation_id: uuid.UUID) -> InvitationRow:
    """Re-read through the list projection so one row and a page cannot disagree."""
    page = await invitations_mod.list_invitations(db, limit=MAX_INVITATION_LOOKUP)
    return next(row for row in page.items if row.id == str(invitation_id))


# ══════════════════════════════════════════════════════════════════════════
#  audit log (FR-208)
# ══════════════════════════════════════════════════════════════════════════
@router.get(
    "/audit-log",
    response_model=Page[AuditRow],
    summary="Filtered, newest-first view of every recorded mutation",
)
async def read_audit_log(
    principal: ReadsAudit,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=audit_log_mod.MAX_PAGE)] = 50,
    cursor: str | None = None,
    actor_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditRow]:
    """`audit:read`, which VIEWER holds and WORKER does not.

    That asymmetry is deliberate (SPEC §8.4): the auditor is a compliance role,
    not a senior one, so it can read the log while holding no write permission
    anywhere.
    """
    return await audit_log_mod.list_entries(
        db,
        limit=limit,
        cursor=cursor,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        since=since,
        until=until,
    )
