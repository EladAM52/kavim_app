"""Outbox claim and dispatch (SPEC §6.8, ADR-005).

The write side is `service.py`; this is the read side, run by the sweeper in
`workers/tasks_notifications.py`.

**`SKIP LOCKED` is what makes the sweep safe on multiple workers.** Each claims a
disjoint batch: `FOR UPDATE SKIP LOCKED` hands rows already locked by another
transaction straight past instead of blocking on them. Without it two workers
either serialise behind each other or, worse, both process the same row and send
two copies of the same message.

**The retry decision comes from the exception type, not the status code.** A
transient failure backs off; a permanent one dead-letters immediately. Getting
that backwards is how a revoked App Password becomes an outage nobody notices for
ten hours (SPEC R14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    DeliveryStatus,
    Locale,
    NotificationChannel,
    OutboxStatus,
)
from app.core.logging import get_logger
from app.core.time import utc_now
from app.integrations.email import (
    EmailAddress,
    EmailError,
    EmailMessage,
    EmailSender,
)
from app.models.notification import MAX_DELIVERY_ATTEMPTS, NotificationDelivery, NotificationOutbox
from app.modules.notifications import quota
from app.modules.notifications.rendering import TemplateMissingError, render_email

logger = get_logger(__name__)

# 1m, 5m, 25m, 2h, 10h — then dead-letter, visible in the admin delivery log.
# Geometric rather than linear: a provider outage that outlasts a minute usually
# outlasts five, and hammering it does not help.
BACKOFF_SCHEDULE: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=25),
    timedelta(hours=2),
    timedelta(hours=10),
)

DEFAULT_BATCH_SIZE = 20


@dataclass(frozen=True, slots=True)
class SweepResult:
    claimed: int = 0
    sent: int = 0
    deferred: int = 0
    failed: int = 0
    dead_lettered: int = 0

    def as_log_fields(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "sent": self.sent,
            "deferred": self.deferred,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
        }


def next_attempt_delay(attempts: int) -> timedelta:
    """Backoff for the *next* try after `attempts` failures so far."""
    index = min(max(attempts - 1, 0), len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[index]


async def claim_batch(
    db: AsyncSession, *, limit: int = DEFAULT_BATCH_SIZE
) -> list[NotificationOutbox]:
    """Take up to `limit` due rows and mark them `processing`.

    `with_for_update(skip_locked=True)` is the whole trick — see the module
    docstring. The status flip is what stops a *later* sweep in the same worker
    from re-claiming a row whose dispatch is still in flight.

    Due-ness is compared with `func.now()`, i.e. **inside the database**, not
    against a Python timestamp. `next_attempt_at` defaults to PostgreSQL's `now()`,
    so comparing it to the host clock compares two different clocks: a worker whose
    host runs a few milliseconds behind the database server sees a just-queued row
    as not yet due and skips it. Measured skew here was only 2 ms, and that was
    already enough to make a freshly queued OTP wait for the next tick.
    """
    rows = list(
        await db.scalars(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status.in_([OutboxStatus.PENDING, OutboxStatus.FAILED]),
                NotificationOutbox.next_attempt_at <= func.now(),
            )
            # Insertion order: an OTP queued before a digest should not wait behind it.
            .order_by(NotificationOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )

    for row in rows:
        row.status = OutboxStatus.PROCESSING
        row.attempts += 1

    return rows


def _build_message(row: NotificationOutbox) -> tuple[EmailMessage, str]:
    """Render an outbox row into a sendable message. Returns ``(message, address)``.

    Raises `TemplateMissingError` or `KeyError` for a malformed payload — both
    permanent, because no amount of retrying will grow a template or invent a
    missing address.
    """
    payload: dict[str, Any] = row.payload or {}
    address = str(payload["to_email"])
    locale = Locale(str(payload.get("locale") or Locale.HE.value))
    context = dict(payload.get("context") or {})

    rendered = render_email(row.event, locale, context)
    message = EmailMessage(
        to=[EmailAddress(address)],
        subject=rendered.subject,
        text_body=rendered.text_body,
        html_body=rendered.html_body,
        locale=locale.value,
    )
    return message, address


def _record_delivery(
    db: AsyncSession,
    row: NotificationOutbox,
    *,
    address: str | None,
    status: DeliveryStatus,
    message_id: str | None = None,
    error: str | None = None,
) -> NotificationDelivery:
    """One row per attempt outcome.

    Skipped and deferred deliveries are recorded too. "No row" and "deliberately
    not sent" must be distinguishable when a manager asks why a worker never got
    the alert.
    """
    delivery = NotificationDelivery(
        outbox_id=row.id,
        # Null for invitation and OTP mail — the account does not exist yet, which
        # is why the column is nullable and `destination` carries the address.
        recipient_id=None,
        event=row.event,
        channel=NotificationChannel.EMAIL,
        destination=address,
        status=status,
        provider_message_id=message_id,
        error=error,
        attempts=row.attempts,
        sent_at=utc_now() if status is DeliveryStatus.SENT else None,
    )
    db.add(delivery)
    return delivery


async def dispatch_row(
    db: AsyncSession,
    row: NotificationOutbox,
    *,
    sender: EmailSender,
    quota_state: quota.QuotaState,
) -> str:
    """Dispatch one claimed row. Returns the outcome as a short label.

    Never raises for an expected failure: the row's own status *is* the error
    channel, and letting one bad message abort the batch would stall every other
    message behind it.
    """
    now = utc_now()
    address: str | None = None

    try:
        message, address = _build_message(row)
    except (TemplateMissingError, KeyError, ValueError) as exc:
        # Permanent. A missing template or a payload without an address cannot be
        # fixed by waiting.
        row.status = OutboxStatus.FAILED
        row.attempts = MAX_DELIVERY_ATTEMPTS
        row.last_error = f"unrenderable: {exc}"
        row.processed_at = now
        _record_delivery(
            db,
            row,
            address=address or "unknown",
            status=DeliveryStatus.FAILED,
            error=str(exc),
        )
        logger.error(
            "outbox_row_unrenderable",
            outbox_id=row.id,
            notification_event=row.event.value,
            error=str(exc),
        )
        return "dead_lettered"

    allowed, refusal = quota.may_send(quota_state, row.event)
    if not allowed:
        # Not a failure: put it back with a delay and record why. Attempts are not
        # spent on a refusal we chose ourselves.
        row.status = OutboxStatus.PENDING
        row.attempts = max(0, row.attempts - 1)
        row.next_attempt_at = now + timedelta(hours=1)
        row.last_error = refusal
        _record_delivery(
            db,
            row,
            address=address,
            status=DeliveryStatus.DEFERRED_QUOTA,
            error=refusal,
        )
        logger.warning(
            "outbox_row_deferred_for_quota",
            outbox_id=row.id,
            notification_event=row.event.value,
            reason=refusal,
        )
        return "deferred"

    try:
        result = await sender.send(message)
    except EmailError as exc:
        retryable = exc.retryable and row.attempts < MAX_DELIVERY_ATTEMPTS
        row.last_error = f"{type(exc).__name__}: {exc}"

        if retryable:
            row.status = OutboxStatus.FAILED
            row.next_attempt_at = now + next_attempt_delay(row.attempts)
            _record_delivery(
                db, row, address=address, status=DeliveryStatus.PENDING, error=str(exc)
            )
            logger.warning(
                "outbox_row_retrying",
                outbox_id=row.id,
                attempts=row.attempts,
                next_attempt_at=row.next_attempt_at.isoformat(),
                error_type=type(exc).__name__,
            )
            return "failed"

        row.status = OutboxStatus.FAILED
        row.attempts = MAX_DELIVERY_ATTEMPTS
        row.processed_at = now
        _record_delivery(db, row, address=address, status=DeliveryStatus.FAILED, error=str(exc))
        # error, not warning: a non-retryable send failure needs somebody to look.
        # An auth failure here means every outbound message is failing.
        logger.error(
            "outbox_row_dead_lettered",
            outbox_id=row.id,
            notification_event=row.event.value,
            error_type=type(exc).__name__,
            error=str(exc),
            retryable=exc.retryable,
        )
        return "dead_lettered"

    except Exception as exc:
        # Anything the provider seam did not classify. The docstring above promises
        # `dispatch_row` never raises, and before this block that promise held only
        # for `EmailError` — a `UnicodeEncodeError` from the success log line inside
        # `send()` escaped, rolled back the transaction, and left the row `pending`
        # after Gmail had already accepted the message. The next tick re-sent it.
        # Observed in the wild: two deliveries, one row, `attempts=0`, and no upper
        # bound on the repeat.
        #
        # The honest position after an unclassified error is that we **do not know**
        # whether the provider accepted it. Both answers are wrong in some case, so
        # the choice is which failure to prefer: a duplicate OTP is an annoyance, a
        # silently lost one locks somebody out. So this retries — but as a normal
        # attempt, which means `MAX_DELIVERY_ATTEMPTS` bounds it and the row
        # dead-letters instead of looping forever.
        row.last_error = f"{type(exc).__name__}: {exc}"
        row.status = OutboxStatus.FAILED

        if row.attempts < MAX_DELIVERY_ATTEMPTS:
            row.next_attempt_at = now + next_attempt_delay(row.attempts)
            _record_delivery(
                db, row, address=address, status=DeliveryStatus.PENDING, error=str(exc)
            )
            logger.error(
                "outbox_row_unclassified_failure",
                outbox_id=row.id,
                notification_event=row.event.value,
                attempts=row.attempts,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return "failed"

        # The ceiling applies here too. Without it "retry an unknown error" becomes
        # "retry forever", which is the same unbounded resend the broad catch was
        # added to prevent — just reached by a different route.
        row.attempts = MAX_DELIVERY_ATTEMPTS
        row.processed_at = now
        _record_delivery(db, row, address=address, status=DeliveryStatus.FAILED, error=str(exc))
        logger.error(
            "outbox_row_dead_lettered",
            outbox_id=row.id,
            notification_event=row.event.value,
            error_type=type(exc).__name__,
            error=str(exc),
            unclassified=True,
        )
        return "dead_lettered"

    row.status = OutboxStatus.DONE
    row.processed_at = now
    row.last_error = None
    _record_delivery(
        db,
        row,
        address=address,
        status=DeliveryStatus.SENT,
        message_id=result.message_id,
    )
    logger.info(
        "outbox_row_sent",
        outbox_id=row.id,
        notification_event=row.event.value,
        dry_run=result.dry_run,
    )
    return "sent"


async def sweep(
    db: AsyncSession,
    *,
    sender: EmailSender,
    limit: int = DEFAULT_BATCH_SIZE,
) -> SweepResult:
    """Claim and dispatch one batch. Caller owns the transaction.

    The caller commits, which is deliberate: the claim and every outcome land
    together, so a worker killed mid-batch leaves rows locked-and-rolled-back —
    i.e. back in `pending` — rather than half-processed.
    """
    rows = await claim_batch(db, limit=limit)
    if not rows:
        return SweepResult()

    quota_state = await quota.current_state(db)
    quota.log_pressure(quota_state)

    tally = {"sent": 0, "deferred": 0, "failed": 0, "dead_lettered": 0}
    for row in rows:
        outcome = await dispatch_row(db, row, sender=sender, quota_state=quota_state)
        tally[outcome] += 1
        if outcome == "sent":
            # Keep the running count honest inside one batch, so a batch larger
            # than the remaining quota does not blow straight through it.
            quota_state = quota.QuotaState(used=quota_state.used + 1, limit=quota_state.limit)

    # Flush before returning. Unlike the write path in `service.py`, a sweep is a
    # complete unit of work: the caller commits immediately after, and any read in
    # the same transaction — the next batch's quota count, a test's `refresh` —
    # must see these outcomes rather than the `processing` state the claim left
    # behind.
    await db.flush()

    return SweepResult(claimed=len(rows), **tally)


async def pending_count(db: AsyncSession) -> int:
    """Depth of the queue, for operational visibility and for tests."""
    total = await db.scalar(
        select(func.count())
        .select_from(NotificationOutbox)
        .where(NotificationOutbox.status.in_([OutboxStatus.PENDING, OutboxStatus.FAILED]))
    )
    return int(total or 0)
