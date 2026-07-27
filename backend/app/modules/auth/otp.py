"""One-time codes (FR-104, SPEC §8.1, §8.3).

Two properties this file is responsible for:

**The code goes to the address on the invitation.** Never to one supplied in the
request. That is what makes it proof of mailbox control rather than a formality,
and it is why `issue_otp` takes an `Invitation` rather than an email string.

**Attempts are counted in the database, not only in Redis.** The Redis limiter is
the cheap first line; `otp_codes.attempts` is the guarantee, incremented in the
same transaction as the check so a burst of concurrent guesses cannot slip past a
read-then-write race.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import Locale, NotificationEvent, OtpChannel, OtpPurpose
from app.core.exceptions import BadRequestError, RateLimitError
from app.core.logging import get_logger
from app.core.security import constant_time_compare, generate_otp, hash_otp
from app.core.time import utc_now
from app.models.auth import Invitation, OtpCode
from app.modules.notifications import service as notifications

logger = get_logger(__name__)


async def issue_otp(
    db: AsyncSession,
    *,
    invitation: Invitation,
    locale: Locale | str,
    request_ip: str | None = None,
) -> OtpCode:
    """Create a code and queue the email carrying it.

    Any earlier unconsumed code for this address and purpose is expired first.
    Leaving several live codes outstanding would multiply an attacker's chances
    per guess for no benefit — a user only ever needs the most recent one.

    The email is **queued**, not sent (CLAUDE.md rule 5). If this transaction
    rolls back, no code was created and no mail goes out.
    """
    now = utc_now()

    superseded = await db.scalars(
        select(OtpCode).where(
            OtpCode.email == invitation.email,
            OtpCode.purpose == OtpPurpose.REGISTRATION,
            OtpCode.consumed_at.is_(None),
            OtpCode.expires_at > now,
        )
    )
    for stale in superseded:
        stale.expires_at = now

    raw_code = generate_otp()
    code = OtpCode(
        email=invitation.email,
        code_hash=hash_otp(raw_code),
        purpose=OtpPurpose.REGISTRATION,
        channel=OtpChannel.EMAIL,
        max_attempts=settings.OTP_MAX_ATTEMPTS,
        expires_at=now + timedelta(minutes=settings.OTP_TTL_MINUTES),
        request_ip=request_ip,
    )
    db.add(code)

    await notifications.queue_email_to_address(
        db,
        event=NotificationEvent.OTP_CODE,
        email=invitation.email,
        locale=str(locale),
        context={"code": raw_code, "ttl_minutes": settings.OTP_TTL_MINUTES},
        entity_type="invitation",
        entity_id=invitation.id,
    )

    # The code itself is never logged — that would put a live credential in the
    # log aggregator. Dry-run mode logs the rendered body deliberately and only
    # when EMAIL_DRY_RUN is on.
    logger.info("otp_issued", email_domain=invitation.email.partition("@")[2])
    return code


async def verify_otp(db: AsyncSession, *, email: str, code: str) -> OtpCode:
    """Consume a code, or raise.

    Raises `BadRequestError` for a wrong or absent code and `RateLimitError` once
    the per-code attempt budget is spent. Both messages are deliberately vague
    about which of "no code exists", "expired", and "wrong digits" occurred.
    """
    now = utc_now()

    candidate = await db.scalar(
        select(OtpCode)
        .where(
            OtpCode.email == email,
            OtpCode.purpose == OtpPurpose.REGISTRATION,
            OtpCode.consumed_at.is_(None),
        )
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )

    if candidate is None or candidate.expires_at <= now:
        raise BadRequestError("That code is not valid. Request a new one.")

    if candidate.attempts >= candidate.max_attempts:
        raise RateLimitError(
            "Too many incorrect attempts for this code. Request a new one.",
            retry_after=settings.OTP_TTL_MINUTES * 60,
        )

    candidate.attempts += 1

    if not constant_time_compare(candidate.code_hash, hash_otp(code)):
        remaining = candidate.max_attempts - candidate.attempts
        # Commit the attempt before raising. `get_db` rolls back on any exception,
        # so without this the increment is undone by the very error it describes —
        # leaving the attempt counter permanently at zero and the code guessable
        # without limit. The commit is deliberate and this is why.
        await db.commit()
        logger.info("otp_mismatch", remaining_attempts=remaining)
        raise BadRequestError("That code is not correct.")

    candidate.consumed_at = now
    return candidate
