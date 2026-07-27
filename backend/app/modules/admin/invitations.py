"""Invitation administration (FR-101, FR-111).

Retires `app/scripts/invite.py` as the only way to invite somebody. Both now go
through `auth.service.invite_user`, so there is one invitation flow rather than
two that can drift.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InvitationStatus
from app.core.exceptions import ConflictError, GoneError, NotFoundError, ValidationError
from app.core.time import utc_now
from app.models.auth import Invitation
from app.models.project import Project
from app.models.role import Role
from app.models.user import User
from app.schemas.admin import InvitationRow
from app.schemas.common import Page, cursor_datetime, decode_cursor, encode_cursor

MAX_PAGE = 200


async def ensure_no_account_exists(db: AsyncSession, email: str) -> None:
    """Refuse to invite somebody who already has an account.

    Registration consumes an invitation to *create* a user, so there is no branch
    for an address that already has one. Without this the invitee would walk the
    whole flow — link, code, form — and fail at the last step with an error that
    reads like a bug.
    """
    existing = await db.scalar(
        select(User.id).where(User.email == email, User.deleted_at.is_(None))
    )
    if existing is not None:
        raise ConflictError("That address already has an account.")


async def validate_project_ids(db: AsyncSession, project_ids: list[uuid.UUID]) -> None:
    """Every named project must exist.

    Phase 4: this must also check that the *inviter* may grant access to these
    projects. Today there is no `projects` module and no membership endpoint, so
    the only thing that can be verified is existence — and an unchecked id would
    become a `project_members` row pointing at nothing on registration.
    """
    if not project_ids:
        return
    found = set(await db.scalars(select(Project.id).where(Project.id.in_(project_ids))))
    missing = sorted(str(pid) for pid in set(project_ids) - found)
    if missing:
        raise ValidationError(
            "Unknown project(s).",
            errors=[{"field": "project_ids", "message": pid} for pid in missing],
        )


async def load_invitation(db: AsyncSession, invitation_id: uuid.UUID) -> Invitation:
    invitation = await db.get(Invitation, invitation_id)
    if invitation is None:
        raise NotFoundError("That invitation does not exist.")
    return invitation


def require_pending(invitation: Invitation) -> None:
    """`410` for spent, `409` for revoked.

    Different because they need different actions: a consumed invitation means
    the person already registered, and a revoked one can simply be re-sent.
    """
    if invitation.status == InvitationStatus.CONSUMED:
        raise GoneError("That invitation has already been used.")
    if invitation.status == InvitationStatus.REVOKED:
        raise ConflictError("That invitation was revoked. Send a new one instead.")


def revoke(invitation: Invitation) -> dict[str, Any]:
    """Idempotent: revoking an already-revoked invitation is a success, not a 409.

    A double-click on the revoke button must not produce an error for an outcome
    the user already has.
    """
    if invitation.status == InvitationStatus.CONSUMED:
        raise GoneError("That invitation has already been used and cannot be revoked.")

    before = {"status": invitation.status.value}
    if invitation.status != InvitationStatus.REVOKED:
        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = utc_now()
    return before


# ══════════════════════════════════════════════════════════════════════════
#  listing
# ══════════════════════════════════════════════════════════════════════════
def _apply_cursor(statement: Select[Any], cursor: str | None) -> Select[Any]:
    if cursor is None:
        return statement
    payload = decode_cursor(cursor)
    try:
        anchor = (cursor_datetime(payload, "created_at"), uuid.UUID(str(payload["id"])))
    except (KeyError, ValueError) as exc:
        raise ValidationError("That page cursor is not valid.") from exc
    return statement.where(tuple_(Invitation.created_at, Invitation.id) < anchor)


async def list_invitations(
    db: AsyncSession,
    *,
    limit: int = 50,
    cursor: str | None = None,
    status: InvitationStatus | None = None,
) -> Page[InvitationRow]:
    limit = max(1, min(limit, MAX_PAGE))

    statement = select(Invitation)
    if status is not None:
        statement = statement.where(Invitation.status == status)

    statement = _apply_cursor(statement, cursor)
    statement = statement.order_by(Invitation.created_at.desc(), Invitation.id.desc()).limit(
        limit + 1
    )

    rows = list(await db.scalars(statement))
    has_more = len(rows) > limit
    rows = rows[:limit]

    role_keys: dict[uuid.UUID, str] = {}
    inviter_names: dict[uuid.UUID, str] = {}
    if rows:
        for role_id, key in await db.execute(
            select(Role.id, Role.key).where(Role.id.in_({row.role_id for row in rows}))
        ):
            role_keys[role_id] = str(key)
        for user_id, name in await db.execute(
            select(User.id, User.full_name).where(User.id.in_({row.invited_by for row in rows}))
        ):
            inviter_names[user_id] = name

    items = [
        InvitationRow(
            id=str(row.id),
            email=row.email,
            role_key=role_keys.get(row.role_id, ""),
            status=_display_status(row),
            project_ids=[str(pid) for pid in (row.project_ids or [])],
            invited_by_name=inviter_names.get(row.invited_by),
            created_at=row.created_at,
            expires_at=row.expires_at,
            consumed_at=row.consumed_at,
            revoked_at=row.revoked_at,
        )
        for row in rows
    ]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({"created_at": last.created_at, "id": str(last.id)})

    return Page[InvitationRow](items=items, next_cursor=next_cursor)


def _display_status(invitation: Invitation) -> InvitationStatus:
    """Report an elapsed invitation as `expired`, not `pending`.

    Nothing sweeps the table to flip the column, so a pending row whose
    `expires_at` has passed is stored as `pending` and is dead in practice. The
    list would otherwise show a link that cannot work, next to a resend button
    the administrator has no reason to press.
    """
    if invitation.status == InvitationStatus.PENDING and invitation.expires_at <= utc_now():
        return InvitationStatus.EXPIRED
    return invitation.status
