"""Password reset (FR-108).

A successful reset revokes every refresh token the user holds. If the reset was
triggered because an account was compromised, leaving old sessions alive would
defeat the entire point of resetting.

The request endpoint returns `202` whether or not the address exists. Anything
else is a user-enumeration oracle, and "we sent a link if that address is
registered" costs a legitimate user nothing.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import NotificationEvent, TokenRevokeReason
from app.core.exceptions import GoneError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    validate_password_strength,
)
from app.core.time import utc_now
from app.models.auth import PasswordResetToken
from app.models.user import User
from app.modules.audit import service as audit
from app.modules.auth import service as auth_service
from app.modules.notifications import service as notifications

logger = get_logger(__name__)


def reset_url(raw_token: str) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/reset-password/{raw_token}"


async def request_reset(
    db: AsyncSession,
    *,
    email: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Queue a reset link if the address belongs to an active account.

    Returns `None` either way, and the caller responds `202` unconditionally. The
    absence of a return value is deliberate — there is nothing for the endpoint to
    branch on, so it cannot accidentally leak the answer.
    """
    user = await db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))

    if user is None or not user.is_active:
        logger.info("password_reset_requested_unknown", email_domain=email.partition("@")[2])
        return

    now = utc_now()

    # Invalidate outstanding tokens. Several live reset links for one account
    # multiply the window in which a leaked inbox is enough to take it over.
    outstanding = await db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
    )
    for stale in outstanding:
        stale.expires_at = now

    raw_token = generate_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES),
            request_ip=ip,
        )
    )

    await notifications.queue_email_to_address(
        db,
        event=NotificationEvent.PASSWORD_RESET,
        email=user.email,
        locale=str(user.locale),
        context={
            "reset_url": reset_url(raw_token),
            "ttl_minutes": settings.PASSWORD_RESET_TTL_MINUTES,
            "full_name": user.full_name,
        },
        entity_type="user",
        entity_id=user.id,
    )

    await audit.write_audit(
        db,
        action=audit.PASSWORD_RESET_REQUESTED,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )


async def confirm_reset(
    db: AsyncSession,
    *,
    raw_token: str,
    new_password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> User:
    """Set the new password and kill every existing session."""
    now = utc_now()
    token = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token))
    )

    if token is None or token.consumed_at is not None or token.expires_at <= now:
        raise GoneError("This reset link has expired or was already used.")

    if problems := validate_password_strength(new_password):
        raise ValidationError(
            "The password does not meet the requirements.",
            errors=[{"field": "password", "message": problem} for problem in problems],
        )

    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise GoneError("This reset link is no longer valid.")

    token.consumed_at = now
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    # A reset is also the escape hatch from a lockout.
    user.failed_login_count = 0
    user.locked_until = None

    revoked = await auth_service.revoke_all_user_tokens(
        db, user.id, TokenRevokeReason.PASSWORD_RESET
    )

    await audit.write_audit(
        db,
        action=audit.PASSWORD_RESET_COMPLETED,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={"sessions_revoked": revoked},
        ip=ip,
        user_agent=user_agent,
    )
    logger.info("password_reset_completed", user_id=str(user.id), sessions_revoked=revoked)
    return user
