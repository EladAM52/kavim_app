"""Gmail send-quota accounting (FR-714, ADR-007).

Free Gmail sends roughly 500 recipients per day. Exceeding it does not bounce a
single message — Google suspends sending for about 24 hours, which would take OTP
and invitation mail down with it. So the ceiling is not a billing concern, it is
an availability one, and the only useful moment to notice is *before* it is hit.

Two behaviours follow:

* **Count recipients, not messages.** That is what Google counts.
* **Urgent mail outranks everything else.** Under pressure, a digest defers and an
  OTP goes. Reversing that trades a nice-to-have for someone locked out of the
  system.

The count comes from `notification_deliveries`, which records what was actually
accepted by the relay rather than what was queued — a queued row that failed never
consumed quota.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import DeliveryStatus, NotificationChannel, NotificationEvent
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.notification import NotificationDelivery
from app.modules.notifications.service import URGENT_EVENTS

logger = get_logger(__name__)

# Warn from 80% so there is room to act — raise the Workspace tier, or turn on
# digests — rather than discovering the ceiling by hitting it.
WARN_AT_FRACTION = 0.8

# Below this many remaining sends, only urgent mail goes out. Sized so a shift
# handover's worth of OTP and invitation traffic still fits after the reserve
# kicks in.
URGENT_RESERVE = 50


@dataclass(frozen=True, slots=True)
class QuotaState:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def fraction_used(self) -> float:
        return self.used / self.limit if self.limit else 1.0

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    @property
    def in_reserve(self) -> bool:
        """Only urgent mail should be sent from here on."""
        return self.remaining <= URGENT_RESERVE


async def current_state(db: AsyncSession) -> QuotaState:
    """Recipients accepted by the relay in the last rolling 24 hours.

    Rolling rather than calendar-day, because Google's window is rolling. A
    midnight reset would let a burst at 23:50 and another at 00:10 both look
    within budget while together tripping the real limit.
    """
    since = utc_now() - timedelta(hours=24)

    used = await db.scalar(
        select(func.count())
        .select_from(NotificationDelivery)
        .where(
            NotificationDelivery.channel == NotificationChannel.EMAIL,
            # BOUNCED counts: the relay accepted it, so the send was spent. Only a
            # message that never left is free.
            NotificationDelivery.status.in_(
                [DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.BOUNCED]
            ),
            NotificationDelivery.created_at >= since,
        )
    )
    return QuotaState(used=int(used or 0), limit=settings.EMAIL_DAILY_QUOTA)


def may_send(state: QuotaState, event: NotificationEvent) -> tuple[bool, str | None]:
    """Whether one more message of this event may go out now.

    Returns ``(allowed, reason)``; `reason` is set only when refused, and is
    recorded on the delivery row so an admin can see *why* nothing arrived rather
    than finding a silent gap.
    """
    urgent = event in URGENT_EVENTS

    if state.exhausted:
        return False, f"daily send quota exhausted ({state.used}/{state.limit})"

    if state.in_reserve and not urgent:
        return False, (
            f"within the urgent-only reserve ({state.remaining} sends left); "
            f"{event.value} deferred so authentication mail still fits"
        )

    return True, None


def log_pressure(state: QuotaState) -> None:
    """Emit a warning as the ceiling approaches.

    Separate from `may_send` so it fires once per sweep rather than once per
    message — a warning repeated 400 times is noise nobody reads.
    """
    if state.exhausted:
        logger.error(
            "email_quota_exhausted",
            used=state.used,
            limit=state.limit,
            hint="sending is suspended for ~24h; OTP and invitation mail is affected",
        )
    elif state.fraction_used >= WARN_AT_FRACTION:
        logger.warning(
            "email_quota_pressure",
            used=state.used,
            limit=state.limit,
            remaining=state.remaining,
            hint="consider Google Workspace (~2000/day) or a transactional provider",
        )
