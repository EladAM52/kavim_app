"""Gmail SMTP implementation of `EmailSender` (ADR-007).

The only file in the project that imports a mail transport.

Authentication is a Google **App Password** over STARTTLS on port 587. Certificate
verification is never disabled — `aiosmtplib` validates by default and there is no
switch here to turn that off, because the one legitimate reason to want it (a
local test sink) is served by `EMAIL_DRY_RUN` instead.
"""

from __future__ import annotations

import uuid
from email.headerregistry import Address
from email.message import EmailMessage as MIMEMessage
from email.utils import format_datetime, make_msgid

import aiosmtplib

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.integrations.email import (
    EmailAuthError,
    EmailMessage,
    EmailPermanentError,
    EmailQuotaError,
    EmailTransientError,
    SendResult,
)

logger = get_logger(__name__)

# SMTP reply codes worth distinguishing. Everything else falls back to the
# 4xx-retryable / 5xx-permanent split.
_AUTH_FAILURE_CODES = frozenset({530, 534, 535})
_QUOTA_CODES = frozenset({550, 552})
_QUOTA_MARKERS = ("5.4.5", "daily user sending limit", "quota")

# Obvious placeholder for a misconfigured development environment. `.invalid` is
# reserved by RFC 2606 precisely so it can never resolve.
_UNSET_FROM = "unconfigured@kavim.invalid"


def _build_mime(message: EmailMessage) -> MIMEMessage:
    """Render to MIME.

    `EmailMessage` from the standard library rather than hand-assembled strings:
    it handles the RFC 2047 header encoding that Hebrew subjects require, and the
    quoted-printable body encoding, correctly. Doing that by hand is how mojibake
    reaches users.
    """
    mime = MIMEMessage()

    from_address = settings.email_from_address
    if "@" not in from_address:
        # Reachable only in dry-run, since `_guard_email` requires a username
        # before real sending. Left as a loud warning rather than an exception so
        # development still works, but visible: an empty `From: Kavim <>` is a
        # malformed message, and silently emitting one teaches nothing.
        logger.warning(
            "email_from_address_unset",
            hint="set SMTP_USERNAME (or EMAIL_FROM_ADDRESS) in .env",
            using=_UNSET_FROM,
        )
        from_address = _UNSET_FROM
    local, _, domain = from_address.partition("@")
    mime["From"] = Address(display_name=settings.EMAIL_FROM_NAME, username=local, domain=domain)
    mime["To"] = ", ".join(str(recipient) for recipient in message.to)
    mime["Subject"] = message.subject
    mime["Date"] = format_datetime(utc_now())
    mime["Message-ID"] = make_msgid(domain=domain or "kavim.local")

    reply_to = message.reply_to or settings.EMAIL_REPLY_TO
    if reply_to:
        mime["Reply-To"] = reply_to

    # Marks the message as transactional, so it is not batched as bulk.
    mime["Auto-Submitted"] = "auto-generated"
    for key, value in message.headers.items():
        mime[key] = value

    mime.set_content(message.text_body, subtype="plain", charset="utf-8")
    if message.html_body:
        mime.add_alternative(message.html_body, subtype="html", charset="utf-8")

    # AFTER the body, not before. `set_content` calls `clear_content()`, which
    # deletes every `Content-*` header — so a Content-Language set earlier is
    # silently dropped and never reaches the recipient. Found by a test that
    # asserted the header was present.
    mime["Content-Language"] = message.locale

    return mime


def _classify(exc: Exception) -> Exception:
    """Map an aiosmtplib failure onto the typed hierarchy in `email.py`.

    The classification is the point of this function: the outbox decides whether
    to retry from the exception type, so getting `535` wrong means a revoked
    App Password is retried into silence instead of alerting.
    """
    code = getattr(exc, "code", None)
    text = str(getattr(exc, "message", "") or exc).lower()

    if isinstance(exc, aiosmtplib.SMTPAuthenticationError) or code in _AUTH_FAILURE_CODES:
        return EmailAuthError(f"SMTP authentication rejected ({code}): check the App Password")

    if code in _QUOTA_CODES and any(marker in text for marker in _QUOTA_MARKERS):
        return EmailQuotaError(f"Gmail send quota exhausted ({code})")

    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        return EmailPermanentError(f"every recipient was refused: {exc}")

    if isinstance(exc, aiosmtplib.SMTPConnectError | aiosmtplib.SMTPTimeoutError | TimeoutError):
        return EmailTransientError(f"SMTP connection problem: {exc}")

    if isinstance(code, int):
        # 4xx is "try again later" by definition; 5xx is a refusal.
        return EmailTransientError(str(exc)) if 400 <= code < 500 else EmailPermanentError(str(exc))

    return EmailTransientError(str(exc))


class SmtpEmailSender:
    """`EmailSender` over Gmail SMTP.

    Stateless: a connection per send. That is the right trade at this volume —
    under 50 workers generates a handful of messages a minute, and a pooled
    connection held open across a quiet period gets closed by Gmail anyway, so
    pooling would add reconnect handling for no measurable gain. Revisit if the
    digest builder ever fans out to hundreds of recipients at once.
    """

    async def send(self, message: EmailMessage) -> SendResult:
        if not settings.EMAIL_ENABLED:
            logger.info(
                "email_suppressed",
                reason="EMAIL_ENABLED is false",
                subject=message.subject,
                recipients=message.recipient_count,
            )
            return SendResult(message_id=f"suppressed-{uuid.uuid4().hex}", accepted=0, dry_run=True)

        mime = _build_mime(message)
        message_id = str(mime["Message-ID"])

        if settings.EMAIL_DRY_RUN:
            # The full rendered body is logged, so the development flow is
            # verifiable end to end without credentials: copy the OTP out of the
            # log and carry on.
            logger.info(
                "email_dry_run",
                message_id=message_id,
                to=[recipient.address for recipient in message.to],
                subject=message.subject,
                locale=message.locale,
                body=message.text_body,
            )
            return SendResult(message_id=message_id, accepted=message.recipient_count, dry_run=True)

        try:
            await aiosmtplib.send(
                mime,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME,
                password=settings.SMTP_PASSWORD.get_secret_value(),
                start_tls=settings.SMTP_STARTTLS,
                use_tls=settings.SMTP_USE_TLS,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            mapped = _classify(exc)
            logger.warning(
                "email_send_failed",
                message_id=message_id,
                error_type=type(mapped).__name__,
                retryable=getattr(mapped, "retryable", True),
                error=str(mapped),
            )
            raise mapped from exc

        logger.info(
            "email_sent",
            message_id=message_id,
            recipients=message.recipient_count,
            subject=message.subject,
        )
        return SendResult(message_id=message_id, accepted=message.recipient_count)


def get_email_sender() -> SmtpEmailSender:
    """The wiring seam. Swapping providers changes this function and nothing else."""
    return SmtpEmailSender()
