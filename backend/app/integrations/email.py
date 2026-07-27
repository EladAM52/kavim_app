"""Provider-neutral email contract (SPEC §6.14, ADR-007).

Everything upstream of this file depends on `EmailSender` and `EmailMessage`, not
on SMTP. That is the whole portability story: Gmail SMTP gives no delivery or
bounce webhooks, so getting real delivery tracking back means swapping the
provider — and this seam is what makes that one new file plus one changed line of
wiring instead of a rewrite.

Nothing here imports a transport library. `smtp_client.py` does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class EmailError(Exception):
    """Base class for send failures.

    Split into retryable and permanent below, because the outbox needs to know
    which it is: retrying a permanent failure five times with backoff turns a
    visible problem into a silent one ten hours later.
    """

    retryable: bool = True


class EmailTransientError(EmailError):
    """Connection refused, timeout, greylisting, `421`, `450`. Retry with backoff."""

    retryable = True


class EmailPermanentError(EmailError):
    """A malformed address, or a `550` rejection. Retrying cannot help."""

    retryable = False


class EmailAuthError(EmailError):
    """`535` — the App Password is wrong or was revoked (SPEC §8.3, R14).

    Deliberately **not** retryable. A revoked credential will not fix itself, and
    five backed-off attempts would turn a total outbound-mail outage into
    something nobody notices until the dead-letter queue is inspected. This must
    surface immediately.
    """

    retryable = False


class EmailQuotaError(EmailError):
    """Gmail's daily send quota is spent (`550 5.4.5`) — FR-714.

    Retryable, but not on the normal backoff curve: nothing will succeed until
    the 24-hour window rolls over.
    """

    retryable = True


@dataclass(frozen=True, slots=True)
class EmailAddress:
    address: str
    name: str | None = None

    def __str__(self) -> str:
        # RFC 5322 display-name form. Quoting matters for Hebrew names, which
        # contain characters that must be MIME-encoded rather than sent raw.
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One message, already rendered and localized.

    Rendering happens in `modules/notifications`, not here — this layer moves
    bytes and knows nothing about events, locales, or templates.

    Both `text_body` and `html_body` are carried: a text alternative is not
    politeness, it materially improves spam scoring, which matters when the
    sender is a `@gmail.com` address writing to a corporate domain (SPEC R13).
    """

    to: Sequence[EmailAddress]
    subject: str
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    # RTL Hebrew bodies need this stated explicitly rather than sniffed.
    locale: str = "he"
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def recipient_count(self) -> int:
        """What counts against the Gmail quota — recipients, not messages."""
        return len(self.to)


@dataclass(frozen=True, slots=True)
class SendResult:
    """What is knowable after an SMTP handoff.

    `message_id` is the value *we* generated and the relay accepted. It is not
    evidence of delivery — SMTP cannot provide that (FR-713). Recording it is
    still worthwhile: it is the only handle for correlating a bounce that arrives
    later in the sending mailbox.
    """

    message_id: str
    accepted: int
    dry_run: bool = False


@runtime_checkable
class EmailSender(Protocol):
    """The seam. Depend on this, never on a concrete client."""

    async def send(self, message: EmailMessage) -> SendResult: ...
