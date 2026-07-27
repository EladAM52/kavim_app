"""Invitation lifecycle: create, validate, consume (FR-101, FR-102, SPEC §8.1).

The invariant this file exists to protect: **the account's email address comes
from the invitation row, never from a form.** An invitation forwarded to someone
else cannot be redeemed under their address, because no code path reads an email
from the request body.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import InvitationStatus, Locale
from app.core.exceptions import GoneError, NotFoundError
from app.core.logging import get_logger
from app.core.security import generate_token, hash_token
from app.core.time import to_local, utc_now
from app.models.auth import Invitation
from app.models.role import Role
from app.models.user import User

logger = get_logger(__name__)


async def create_invitation(
    db: AsyncSession,
    *,
    email: str,
    role_id: uuid.UUID,
    invited_by: uuid.UUID,
    project_ids: list[uuid.UUID] | None = None,
) -> tuple[Invitation, str]:
    """Returns ``(row, raw_token)``.

    The raw token is returned rather than stored: only its SHA-256 digest is
    persisted, so a database dump yields no usable invitation link. This is the
    single moment the raw value exists, and the caller must hand it straight to
    the outbox payload.

    Any existing pending invitation for the address is superseded first — the
    partial unique index permits exactly one live invitation per email, so a
    resend would otherwise violate it (FR-111).

    **Order matters, and getting it wrong is a 500.** The old row must be marked
    revoked *and flushed* before the new one is inserted. Adding the new row
    first leaves two `pending` rows for one address at flush time, which is
    exactly what `uq_invitations_pending_email` forbids — so re-inviting an
    address that already had a live invitation raised `UniqueViolationError`
    rather than superseding it.
    """
    existing = await db.scalar(
        select(Invitation).where(
            Invitation.email == email,
            Invitation.status == InvitationStatus.PENDING,
        )
    )

    if existing is not None:
        existing.status = InvitationStatus.REVOKED
        existing.revoked_at = utc_now()
        # Clears the partial unique index before the replacement is inserted.
        await db.flush()

    raw_token = generate_token()
    invitation = Invitation(
        email=email,
        token_hash=hash_token(raw_token),
        role_id=role_id,
        project_ids=project_ids or [],
        invited_by=invited_by,
        status=InvitationStatus.PENDING,
        expires_at=utc_now() + timedelta(days=settings.INVITATION_TTL_DAYS),
    )
    db.add(invitation)

    if existing is not None:
        # A second flush, because `superseded_by` needs the new row's id.
        await db.flush()
        existing.superseded_by = invitation.id
        logger.info("invitation_superseded", email=email, superseded_by=str(invitation.id))

    return invitation, raw_token


async def load_valid_invitation(db: AsyncSession, raw_token: str) -> Invitation:
    """Look up a pending, unexpired invitation by raw token.

    Raises `GoneError` (410) for an invitation that existed but is spent or stale,
    and `NotFoundError` (404) for a token that never existed. The distinction is
    deliberate and safe to make: both require possessing a 256-bit token, so
    neither response is an oracle, and "your link expired" is materially more
    useful to the invitee than "not found".
    """
    invitation = await db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(raw_token))
    )
    if invitation is None:
        raise NotFoundError("This invitation link is not recognized.")

    if invitation.status is InvitationStatus.CONSUMED:
        raise GoneError("This invitation has already been used.")
    if invitation.status is InvitationStatus.REVOKED:
        raise GoneError("This invitation was cancelled. Ask your manager for a new one.")
    if invitation.expires_at <= utc_now():
        # Persist the transition so the row stops claiming to be pending, and so
        # the partial unique index frees the address for a fresh invitation.
        invitation.status = InvitationStatus.EXPIRED
        raise GoneError("This invitation has expired. Ask your manager for a new one.")
    if invitation.status is InvitationStatus.EXPIRED:
        raise GoneError("This invitation has expired. Ask your manager for a new one.")

    return invitation


async def load_invitation_by_id(db: AsyncSession, invitation_id: uuid.UUID) -> Invitation:
    """Re-load by id, for the registration step.

    After OTP verification the raw token is never transmitted again (SPEC §8.1);
    the registration ticket carries this id instead. The same validity checks
    apply — a manager who revokes an invitation between OTP and registration must
    still stop the registration.
    """
    invitation = await db.get(Invitation, invitation_id)
    if invitation is None:
        raise NotFoundError("This invitation is not recognized.")
    if invitation.status is not InvitationStatus.PENDING:
        raise GoneError("This invitation is no longer available.")
    if invitation.expires_at <= utc_now():
        invitation.status = InvitationStatus.EXPIRED
        raise GoneError("This invitation has expired. Ask your manager for a new one.")
    return invitation


def mark_consumed(invitation: Invitation, user_id: uuid.UUID) -> None:
    """Single-use enforcement. Called inside the registration transaction."""
    invitation.status = InvitationStatus.CONSUMED
    invitation.consumed_at = utc_now()
    invitation.consumed_by = user_id


async def build_preview_context(db: AsyncSession, invitation: Invitation) -> tuple[Role, str]:
    """Returns ``(role, inviter_name)`` for the landing screen and email copy.

    Loaded explicitly rather than through relationships: every relationship in
    this project is `lazy="raise_on_sql"`, so an implicit load raises instead of
    silently issuing a query under async.
    """
    role = await db.get(Role, invitation.role_id)
    if role is None:  # pragma: no cover - FK is RESTRICT, so unreachable
        raise NotFoundError("The invited role no longer exists.")

    inviter = await db.get(User, invitation.invited_by)
    inviter_name = inviter.full_name if inviter is not None else "Kavim"
    return role, inviter_name


def role_label(role: Role, locale: Locale | str) -> str:
    return role.label_he if str(locale) == Locale.HE.value else role.label_en


def registration_url(raw_token: str) -> str:
    """The link that goes in the email.

    Points at the SPA route, not the API: the invitee lands on a screen that
    then calls `GET /auth/invitations/{token}`.
    """
    return f"{settings.APP_BASE_URL.rstrip('/')}/invite/{raw_token}"


def format_expiry(invitation: Invitation, locale: Locale | str) -> str:
    """Human-readable expiry in Asia/Jerusalem.

    Rendered here rather than in the template because the template has no access
    to the timezone rules, and a UTC timestamp shown to a worker in Israel is off
    by two or three hours depending on the season.
    """
    local = to_local(invitation.expires_at)
    return (
        local.strftime("%d/%m/%Y %H:%M")
        if str(locale) == Locale.HE.value
        else local.strftime("%Y-%m-%d %H:%M")
    )
