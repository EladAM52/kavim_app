"""The outbox write path (CLAUDE.md rule 5, ADR-005).

This is the **only** way application code sends anything. A request handler never
calls a provider and never calls `.delay()`; it writes a `notification_outbox` row
in the same transaction as the domain change, and the sweeper in
`workers/tasks_notifications.py` dispatches it.

Why that matters concretely: an invitation whose transaction rolls back must not
produce an email inviting someone to an account that does not exist, and an
invitation that commits must not lose its email because Celery was briefly
unreachable. Writing the row inside the transaction makes both impossible rather
than unlikely.

Nothing here sends. Nothing here imports a transport.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import NotificationEvent
from app.core.logging import get_logger
from app.models.notification import NotificationOutbox

logger = get_logger(__name__)

# Events that must not wait for the next sweep tick and must never be
# deferred by quiet hours: somebody is sitting at a form waiting for the code.
URGENT_EVENTS = frozenset(
    {
        NotificationEvent.OTP_CODE,
        NotificationEvent.INVITATION,
        NotificationEvent.PASSWORD_RESET,
        NotificationEvent.ACCOUNT_LOCKED,
    }
)


async def queue_notification(
    db: AsyncSession,
    *,
    event: NotificationEvent,
    payload: dict[str, Any],
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    triggered_by: uuid.UUID | None = None,
) -> NotificationOutbox:
    """Write one outbox row. Caller owns the transaction.

    Deliberately does **not** flush or commit: the point is that this row shares
    the caller's transaction. A `commit()` here would break the guarantee the
    whole design exists to provide.

    The payload must carry everything the dispatcher needs to render without
    re-reading the domain, because by dispatch time the row it described may have
    changed or been deleted.
    """
    row = NotificationOutbox(
        event=event,
        payload=payload,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        triggered_by=triggered_by,
    )
    db.add(row)

    # `notification_event`, not `event`: structlog's bound-logger methods take the
    # message as a parameter literally named `event`, so an `event=` keyword is a
    # TypeError for multiple values — at the first call, i.e. at runtime.
    logger.debug(
        "notification_queued",
        notification_event=event.value,
        entity_type=entity_type,
        urgent=event in URGENT_EVENTS,
    )
    return row


async def queue_email_to_address(
    db: AsyncSession,
    *,
    event: NotificationEvent,
    email: str,
    locale: str,
    context: dict[str, Any],
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> NotificationOutbox:
    """Queue mail to a bare address rather than to a user row.

    Needed because the auth flow sends to people who do not have accounts yet —
    an invitation and its OTP both precede the `users` row. Recipient resolution
    for domain events (assignment, mention) works from user ids instead and lands
    with the rest of Phase 7.
    """
    return await queue_notification(
        db,
        event=event,
        payload={
            "channel": "email",
            "to_email": email,
            "locale": locale,
            "context": context,
        },
        entity_type=entity_type,
        entity_id=entity_id,
    )
