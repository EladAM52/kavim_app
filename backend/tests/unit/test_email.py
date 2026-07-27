"""Email rendering and SMTP error classification (ADR-007).

No test here opens a socket. The dry-run path is exercised directly, and the
error mapping is tested by classifying constructed exceptions — because the
behaviour that matters is *which* failures are retryable, and that decision is
what stands between a revoked App Password and a silent outbound-mail outage.
"""

from __future__ import annotations

import aiosmtplib
import pytest

from app.core.enums import Locale, NotificationEvent
from app.integrations.email import (
    EmailAddress,
    EmailAuthError,
    EmailMessage,
    EmailPermanentError,
    EmailQuotaError,
    EmailTransientError,
)
from app.integrations.smtp_client import SmtpEmailSender, _build_mime, _classify
from app.modules.notifications.rendering import (
    TemplateMissingError,
    available_events,
    render_email,
)
from app.modules.notifications.service import URGENT_EVENTS


# ══════════════════════════════════════════════════════════════════════════
#  rendering
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("locale", [Locale.HE, Locale.EN])
def test_otp_template_renders_in_both_locales(locale: Locale) -> None:
    rendered = render_email(
        NotificationEvent.OTP_CODE, locale, {"code": "482913", "ttl_minutes": 10}
    )

    assert "482913" in rendered.subject
    assert "482913" in rendered.text_body
    assert rendered.html_body is not None
    # The code must be direction-isolated, or a 6-digit number inside Hebrew text
    # can render in the wrong order and be unusable.
    assert 'dir="ltr"' in rendered.html_body


def test_hebrew_template_is_actually_hebrew() -> None:
    """Guards against the encoding damage that hit `seed.py` once already."""
    rendered = render_email(
        NotificationEvent.OTP_CODE, Locale.HE, {"code": "111111", "ttl_minutes": 10}
    )
    assert "קוד האימות" in rendered.text_body
    assert 'dir="rtl"' in (rendered.html_body or "")


def test_invitation_template_carries_the_link_and_expiry() -> None:
    rendered = render_email(
        NotificationEvent.INVITATION,
        Locale.HE,
        {
            "invited_by_name": "מנהל קו",
            "role_label": "עובד",
            "registration_url": "https://kavim.example.com/invite/abc123",
            "expires_at_local": "01/08/2026 12:00",
        },
    )
    assert "https://kavim.example.com/invite/abc123" in rendered.text_body
    # Repeated in the HTML as bare text too, because a stripped button leaves
    # nothing clickable.
    assert (rendered.html_body or "").count("abc123") >= 2


def test_a_subject_is_collapsed_to_one_line() -> None:
    """A multi-line subject produces a malformed header."""
    rendered = render_email(
        NotificationEvent.OTP_CODE, Locale.EN, {"code": "123456", "ttl_minutes": 5}
    )
    assert "\n" not in rendered.subject
    assert "\r" not in rendered.subject


def test_a_missing_variable_fails_loudly() -> None:
    """StrictUndefined, not silent blanks. Mailing someone a verification message
    with an empty space where the code belongs is worse than an error."""
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        render_email(NotificationEvent.OTP_CODE, Locale.HE, {"ttl_minutes": 10})


def test_an_event_with_no_template_raises_rather_than_falling_back() -> None:
    """A Hebrew speaker receiving English mail is a bug someone should see, so
    there is no silent fallback."""
    with pytest.raises(TemplateMissingError):
        render_email(NotificationEvent.TASK_ASSIGNED, Locale.HE, {})


def test_every_urgent_event_has_copy_in_both_locales() -> None:
    """The events auth queues must be renderable *now* — a missing template only
    surfaces at dispatch, which is after the user is already waiting."""
    directories = available_events()

    for event in sorted(URGENT_EVENTS, key=lambda item: item.value):
        assert event.value in directories, f"no template directory for {event.value}"
        for locale in (Locale.HE, Locale.EN):
            rendered = render_email(
                event,
                locale,
                {
                    "code": "000000",
                    "ttl_minutes": 10,
                    "minutes": 15,
                    "reason": "failed_logins",
                    "full_name": "Test Person",
                    "reset_url": "https://kavim.example.com/reset-password/tok",
                    "invited_by_name": "Manager",
                    "role_label": "Worker",
                    "registration_url": "https://kavim.example.com/invite/tok",
                    "expires_at_local": "01/08/2026 12:00",
                },
            )
            assert rendered.subject, f"{event.value}/{locale.value} has an empty subject"
            assert rendered.text_body, f"{event.value}/{locale.value} has an empty body"


def test_account_locked_template_branches_on_reason() -> None:
    lockout = render_email(
        NotificationEvent.ACCOUNT_LOCKED,
        Locale.EN,
        {"reason": "failed_logins", "minutes": 15},
    )
    reuse = render_email(
        NotificationEvent.ACCOUNT_LOCKED,
        Locale.EN,
        {"reason": "token_reuse", "minutes": 15},
    )

    assert "failed sign-in" in lockout.text_body
    assert "reused session token" in reuse.text_body
    assert lockout.text_body != reuse.text_body


# ══════════════════════════════════════════════════════════════════════════
#  MIME assembly
# ══════════════════════════════════════════════════════════════════════════
def test_hebrew_subject_is_header_encoded() -> None:
    """Raw UTF-8 in a header is not legal; RFC 2047 encoding is what makes a
    Hebrew subject arrive readable."""
    mime = _build_mime(
        EmailMessage(
            to=[EmailAddress("worker@example.com", "עובד")],
            subject="קוד האימות שלך: 123456",
            text_body="גוף ההודעה",
        )
    )
    raw = mime.as_string()
    assert "=?utf-8?" in raw, "the Hebrew subject was not MIME-encoded"
    assert mime["Message-ID"]
    assert mime["Content-Language"] == "he"


def test_an_html_body_produces_a_multipart_alternative() -> None:
    """A text alternative materially improves spam scoring, which matters when the
    sender is a @gmail.com address writing to a corporate domain (SPEC R13)."""
    mime = _build_mime(
        EmailMessage(
            to=[EmailAddress("worker@example.com")],
            subject="Subject",
            text_body="plain",
            html_body="<p>rich</p>",
        )
    )
    assert mime.is_multipart()
    subtypes = {part.get_content_subtype() for part in mime.iter_parts()}
    assert {"plain", "html"} <= subtypes


# ══════════════════════════════════════════════════════════════════════════
#  error classification — the retry decision
# ══════════════════════════════════════════════════════════════════════════
def test_authentication_failure_is_not_retryable() -> None:
    """`535` means the App Password was revoked. Retrying five times with backoff
    turns a total outbound-mail outage into one nobody notices for ten hours
    (SPEC R14)."""
    mapped = _classify(
        aiosmtplib.SMTPAuthenticationError(535, "Username and Password not accepted")
    )

    assert isinstance(mapped, EmailAuthError)
    assert mapped.retryable is False


def test_quota_exhaustion_is_recognized() -> None:
    exc = aiosmtplib.SMTPResponseException(550, "5.4.5 Daily user sending limit exceeded")
    mapped = _classify(exc)

    assert isinstance(mapped, EmailQuotaError)


def test_a_4xx_reply_is_retryable_and_5xx_is_not() -> None:
    transient = _classify(aiosmtplib.SMTPResponseException(451, "Try again later"))
    permanent = _classify(aiosmtplib.SMTPResponseException(553, "Mailbox name not allowed"))

    assert isinstance(transient, EmailTransientError)
    assert transient.retryable is True
    assert isinstance(permanent, EmailPermanentError)
    assert permanent.retryable is False


def test_a_connection_problem_is_retryable() -> None:
    assert isinstance(_classify(aiosmtplib.SMTPConnectError("refused")), EmailTransientError)
    assert isinstance(_classify(TimeoutError("timed out")), EmailTransientError)


def test_an_unrecognized_failure_is_treated_as_retryable() -> None:
    """The safe default: retrying a permanent failure wastes attempts, but
    permanently dropping a recoverable one loses an OTP a user is waiting for."""
    assert isinstance(_classify(RuntimeError("something odd")), EmailTransientError)


# ══════════════════════════════════════════════════════════════════════════
#  dry run
# ══════════════════════════════════════════════════════════════════════════
async def test_dry_run_reports_success_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The development default. No credentials, no socket — so the whole auth flow
    is testable before a Gmail App Password exists."""
    from app.core import config

    monkeypatch.setattr(config.settings, "EMAIL_ENABLED", True)
    monkeypatch.setattr(config.settings, "EMAIL_DRY_RUN", True)
    monkeypatch.setattr(config.settings, "SMTP_USERNAME", "kavimsupport@gmail.com")

    async def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry run must not open a connection")

    monkeypatch.setattr(aiosmtplib, "send", _explode)

    result = await SmtpEmailSender().send(
        EmailMessage(
            to=[EmailAddress("worker@example.com")],
            subject="Subject",
            text_body="body",
        )
    )

    assert result.dry_run is True
    assert result.accepted == 1
    assert result.message_id


async def test_disabled_email_suppresses_without_rendering_a_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "EMAIL_ENABLED", False)

    result = await SmtpEmailSender().send(
        EmailMessage(
            to=[EmailAddress("worker@example.com")],
            subject="Subject",
            text_body="body",
        )
    )

    assert result.dry_run is True
    assert result.accepted == 0
