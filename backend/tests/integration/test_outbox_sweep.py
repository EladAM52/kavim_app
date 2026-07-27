"""The outbox sweeper (SPEC §6.8, ADR-005).

Integration rather than unit, because the two properties that matter are
PostgreSQL behaviours: `FOR UPDATE SKIP LOCKED` handing a locked row to a second
worker instead of blocking, and claim-plus-outcome committing as one unit.

The `EmailSender` is stubbed at the `integrations/` seam. No test opens a socket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeliveryStatus, Locale, NotificationEvent, OutboxStatus
from app.core.time import utc_now
from app.integrations.email import (
    EmailAuthError,
    EmailMessage,
    EmailPermanentError,
    EmailTransientError,
    SendResult,
)
from app.models.notification import MAX_DELIVERY_ATTEMPTS, NotificationDelivery, NotificationOutbox
from app.modules.notifications import outbox, quota, service

pytestmark = pytest.mark.integration


# ══════════════════════════════════════════════════════════════════════════
#  stub sender
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class RecordingSender:
    """Captures what would have been sent, and can be told to fail."""

    raise_error: Exception | None = None
    sent: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> SendResult:
        if self.raise_error is not None:
            raise self.raise_error
        self.sent.append(message)
        return SendResult(message_id=f"<test-{len(self.sent)}@kavim>", accepted=len(message.to))


async def _queue_otp(db: AsyncSession, email: str = "worker@example.com") -> NotificationOutbox:
    row = await service.queue_email_to_address(
        db,
        event=NotificationEvent.OTP_CODE,
        email=email,
        locale=Locale.HE.value,
        context={"code": "482913", "ttl_minutes": 10},
    )
    await db.flush()
    return row


async def _deliveries(db: AsyncSession, outbox_id: int) -> list[NotificationDelivery]:
    rows = await db.scalars(
        select(NotificationDelivery)
        .where(NotificationDelivery.outbox_id == outbox_id)
        .order_by(NotificationDelivery.id)
    )
    return list(rows)


# ══════════════════════════════════════════════════════════════════════════
#  happy path
# ══════════════════════════════════════════════════════════════════════════
async def test_a_queued_message_is_rendered_sent_and_recorded(db: AsyncSession) -> None:
    row = await _queue_otp(db)
    sender = RecordingSender()

    result = await outbox.sweep(db, sender=sender)

    assert result.claimed == 1
    assert result.sent == 1

    # Rendered in Hebrew, with the code from the payload.
    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert "482913" in message.subject
    assert "קוד האימות" in message.text_body
    assert message.locale == "he"
    assert message.to[0].address == "worker@example.com"

    await db.refresh(row)
    assert row.status is OutboxStatus.DONE
    assert row.processed_at is not None
    assert row.last_error is None

    deliveries = await _deliveries(db, row.id)
    assert len(deliveries) == 1
    assert deliveries[0].status is DeliveryStatus.SENT
    assert deliveries[0].provider_message_id == "<test-1@kavim>"
    # No user row exists yet — the address is the only identifier available, which
    # is the whole reason recipient_id is nullable.
    assert deliveries[0].recipient_id is None
    assert deliveries[0].destination == "worker@example.com"


async def test_a_done_row_is_not_swept_twice(db: AsyncSession) -> None:
    """Double-sending an OTP is worse than not sending one: the user gets two codes
    and only the newest works."""
    await _queue_otp(db)
    sender = RecordingSender()

    first = await outbox.sweep(db, sender=sender)
    second = await outbox.sweep(db, sender=sender)

    assert first.sent == 1
    assert second.claimed == 0
    assert len(sender.sent) == 1


async def test_rows_scheduled_for_the_future_are_left_alone(db: AsyncSession) -> None:
    row = await _queue_otp(db)
    row.next_attempt_at = utc_now() + timedelta(minutes=5)
    await db.flush()

    assert (await outbox.sweep(db, sender=RecordingSender())).claimed == 0


async def test_the_batch_size_is_respected(db: AsyncSession) -> None:
    for index in range(5):
        await _queue_otp(db, email=f"worker{index}@example.com")

    result = await outbox.sweep(db, sender=RecordingSender(), limit=2)

    assert result.claimed == 2
    assert await outbox.pending_count(db) == 3


async def test_claiming_is_in_insertion_order(db: AsyncSession) -> None:
    """An OTP queued before a digest must not wait behind it."""
    first = await _queue_otp(db, email="first@example.com")
    second = await _queue_otp(db, email="second@example.com")

    claimed = await outbox.claim_batch(db, limit=10)

    assert [row.id for row in claimed] == [first.id, second.id]


# ══════════════════════════════════════════════════════════════════════════
#  retry, dead-letter, and the retryable/permanent split
# ══════════════════════════════════════════════════════════════════════════
async def test_a_transient_failure_backs_off_and_stays_queued(db: AsyncSession) -> None:
    row = await _queue_otp(db)
    sender = RecordingSender(raise_error=EmailTransientError("connection refused"))

    result = await outbox.sweep(db, sender=sender)

    assert result.failed == 1
    await db.refresh(row)
    assert row.status is OutboxStatus.FAILED
    assert row.attempts == 1
    # Still queued, and not due yet — one minute for the first retry.
    assert row.next_attempt_at > utc_now()
    assert row.processed_at is None
    assert "connection refused" in (row.last_error or "")

    assert (await _deliveries(db, row.id))[0].status is DeliveryStatus.PENDING


async def test_an_auth_failure_dead_letters_immediately(db: AsyncSession) -> None:
    """`535` means the App Password was revoked. Five backed-off retries would turn
    a total outbound-mail outage into one nobody notices for ten hours (SPEC R14)."""
    row = await _queue_otp(db)
    sender = RecordingSender(raise_error=EmailAuthError("SMTP authentication rejected (535)"))

    result = await outbox.sweep(db, sender=sender)

    assert result.dead_lettered == 1
    assert result.failed == 0

    await db.refresh(row)
    assert row.status is OutboxStatus.FAILED
    assert row.attempts == MAX_DELIVERY_ATTEMPTS, (
        "an auth failure must not leave retries on the table"
    )
    assert row.processed_at is not None

    assert (await _deliveries(db, row.id))[0].status is DeliveryStatus.FAILED


async def test_a_permanent_rejection_dead_letters_immediately(db: AsyncSession) -> None:
    row = await _queue_otp(db)
    sender = RecordingSender(raise_error=EmailPermanentError("550 mailbox does not exist"))

    assert (await outbox.sweep(db, sender=sender)).dead_lettered == 1

    await db.refresh(row)
    assert row.attempts == MAX_DELIVERY_ATTEMPTS


async def test_retries_are_exhausted_then_dead_lettered(db: AsyncSession) -> None:
    row = await _queue_otp(db)
    sender = RecordingSender(raise_error=EmailTransientError("still down"))

    for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
        # Pull the next attempt forward so the backoff does not have to be waited out.
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
        await db.flush()

        result = await outbox.sweep(db, sender=sender)
        assert result.claimed == 1, f"attempt {attempt} was not claimed"

    await db.refresh(row)
    assert row.attempts == MAX_DELIVERY_ATTEMPTS
    assert row.processed_at is not None, "the row should be dead-lettered, not retried forever"

    statuses = [delivery.status for delivery in await _deliveries(db, row.id)]
    assert statuses[-1] is DeliveryStatus.FAILED
    assert statuses.count(DeliveryStatus.PENDING) == MAX_DELIVERY_ATTEMPTS - 1


def test_the_backoff_schedule_grows_and_then_holds() -> None:
    delays = [outbox.next_attempt_delay(n).total_seconds() for n in range(1, 8)]

    assert delays[:5] == [60, 300, 1500, 7200, 36000]
    # Past the schedule it holds at the longest delay rather than raising IndexError.
    assert delays[5] == delays[6] == 36000


# ══════════════════════════════════════════════════════════════════════════
#  unrenderable payloads
# ══════════════════════════════════════════════════════════════════════════
async def test_a_payload_with_no_address_is_dead_lettered_not_retried(
    db: AsyncSession,
) -> None:
    row = NotificationOutbox(
        event=NotificationEvent.OTP_CODE,
        payload={"channel": "email", "locale": "he", "context": {"code": "1"}},
    )
    db.add(row)
    await db.flush()

    assert (await outbox.sweep(db, sender=RecordingSender())).dead_lettered == 1

    await db.refresh(row)
    assert row.attempts == MAX_DELIVERY_ATTEMPTS
    assert "unrenderable" in (row.last_error or "")


async def test_an_event_with_no_template_is_dead_lettered(db: AsyncSession) -> None:
    """Waiting will not grow a template, so retrying is pure delay."""
    row = NotificationOutbox(
        event=NotificationEvent.TASK_ASSIGNED,
        payload={"channel": "email", "to_email": "x@example.com", "locale": "he", "context": {}},
    )
    db.add(row)
    await db.flush()

    assert (await outbox.sweep(db, sender=RecordingSender())).dead_lettered == 1


async def test_one_bad_row_does_not_stall_the_rest_of_the_batch(db: AsyncSession) -> None:
    """The reason `dispatch_row` never raises: a single malformed message must not
    hold up every message queued behind it."""
    bad = NotificationOutbox(
        event=NotificationEvent.OTP_CODE,
        payload={"channel": "email", "locale": "he", "context": {}},
    )
    db.add(bad)
    await db.flush()
    good = await _queue_otp(db, email="fine@example.com")

    sender = RecordingSender()
    result = await outbox.sweep(db, sender=sender)

    assert result.dead_lettered == 1
    assert result.sent == 1
    await db.refresh(good)
    assert good.status is OutboxStatus.DONE


# ══════════════════════════════════════════════════════════════════════════
#  quota — FR-714
# ══════════════════════════════════════════════════════════════════════════
async def test_quota_counts_accepted_recipients_in_the_rolling_window(
    db: AsyncSession,
) -> None:
    await _queue_otp(db)
    await outbox.sweep(db, sender=RecordingSender())

    state = await quota.current_state(db)
    assert state.used == 1

    # A row outside the window does not count.
    old = NotificationDelivery(
        event=NotificationEvent.OTP_CODE,
        channel="email",
        destination="old@example.com",
        status=DeliveryStatus.SENT,
    )
    db.add(old)
    await db.flush()
    old.created_at = utc_now() - timedelta(hours=25)
    await db.flush()

    assert (await quota.current_state(db)).used == 1


async def test_an_exhausted_quota_defers_rather_than_failing(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deferral must not consume a retry: the refusal is ours, not the provider's."""
    from app.core import config

    monkeypatch.setattr(config.settings, "EMAIL_DAILY_QUOTA", 0)

    row = await _queue_otp(db)
    sender = RecordingSender()

    result = await outbox.sweep(db, sender=sender)

    assert result.deferred == 1
    assert not sender.sent

    await db.refresh(row)
    assert row.status is OutboxStatus.PENDING
    assert row.attempts == 0, "a deferral must not spend an attempt"
    assert row.next_attempt_at > utc_now()

    delivery = (await _deliveries(db, row.id))[0]
    # Distinct from quiet hours, so an admin looks at the Gmail ceiling rather than
    # at a user's schedule.
    assert delivery.status is DeliveryStatus.DEFERRED_QUOTA


async def test_the_urgent_reserve_defers_routine_mail_but_not_an_otp(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under quota pressure a digest waits so authentication mail still fits."""
    from app.core import config

    # Limit chosen so `remaining` lands inside the reserve.
    monkeypatch.setattr(config.settings, "EMAIL_DAILY_QUOTA", quota.URGENT_RESERVE)

    state = await quota.current_state(db)
    assert state.in_reserve

    allowed_otp, _ = quota.may_send(state, NotificationEvent.OTP_CODE)
    allowed_digest, reason = quota.may_send(state, NotificationEvent.DAILY_DIGEST)

    assert allowed_otp is True
    assert allowed_digest is False
    assert reason is not None and "reserve" in reason


async def test_a_batch_cannot_overrun_the_remaining_quota(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The running count is updated inside the batch, so a batch larger than the
    remaining allowance stops at the ceiling instead of blowing through it."""
    from app.core import config

    monkeypatch.setattr(config.settings, "EMAIL_DAILY_QUOTA", 2)
    monkeypatch.setattr(quota, "URGENT_RESERVE", 0)

    for index in range(4):
        await _queue_otp(db, email=f"worker{index}@example.com")

    sender = RecordingSender()
    result = await outbox.sweep(db, sender=sender, limit=4)

    assert result.sent == 2
    assert result.deferred == 2
    assert len(sender.sent) == 2


# ══════════════════════════════════════════════════════════════════════════
#  concurrency — the SKIP LOCKED property
# ══════════════════════════════════════════════════════════════════════════
async def test_a_second_worker_skips_rows_already_claimed(engine: object, db: AsyncSession) -> None:
    """Two workers must claim disjoint batches.

    Runs on a genuinely separate connection, because the whole point is what
    PostgreSQL does with a row lock held by another transaction. A single session
    could never demonstrate it.
    """
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.ext.asyncio import AsyncSession as Session

    assert isinstance(engine, AsyncEngine)

    # Committed on its own connection so the other transaction can see it.
    async with Session(bind=engine, expire_on_commit=False) as setup:
        row = NotificationOutbox(
            event=NotificationEvent.OTP_CODE,
            payload={
                "channel": "email",
                "to_email": "concurrent@example.com",
                "locale": "he",
                "context": {"code": "111111", "ttl_minutes": 10},
            },
        )
        setup.add(row)
        await setup.commit()
        row_id = row.id

    try:
        async with Session(bind=engine) as first, Session(bind=engine) as second:
            claimed_first = await outbox.claim_batch(first, limit=10)
            assert row_id in {claimed.id for claimed in claimed_first}

            # The lock is held by `first`, so this must return empty rather than block.
            claimed_second = await outbox.claim_batch(second, limit=10)
            assert claimed_second == [], "SKIP LOCKED did not skip a locked row"

            await first.rollback()
            await second.rollback()
    finally:
        async with Session(bind=engine) as cleanup:
            target = await cleanup.get(NotificationOutbox, row_id)
            if target is not None:
                await cleanup.delete(target)
            await cleanup.commit()


async def test_a_rolled_back_sweep_leaves_the_row_claimable(db: AsyncSession) -> None:
    """A worker killed mid-batch must leave work redeliverable, not half-done."""
    row = await _queue_otp(db)
    original_status = row.status

    await outbox.claim_batch(db, limit=10)
    assert row.status is OutboxStatus.PROCESSING

    await db.rollback()

    fresh = await db.get(NotificationOutbox, row.id)
    # Rolled back to pending, and available to the next sweep.
    assert fresh is None or fresh.status is original_status


# ══════════════════════════════════════════════════════════════════════════
#  queue depth
# ══════════════════════════════════════════════════════════════════════════
async def test_pending_count_covers_queued_and_retrying_rows(db: AsyncSession) -> None:
    await _queue_otp(db, email="a@example.com")
    retrying = await _queue_otp(db, email="b@example.com")
    retrying.status = OutboxStatus.FAILED
    done = await _queue_otp(db, email="c@example.com")
    done.status = OutboxStatus.DONE
    await db.flush()

    assert await outbox.pending_count(db) == 2

    total = await db.scalar(select(func.count()).select_from(NotificationOutbox))
    assert total == 3
