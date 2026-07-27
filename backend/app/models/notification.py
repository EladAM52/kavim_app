"""Notification pipeline: outbox, deliveries, in-app feed (SPEC §6.8, ADR-005).

The **transactional outbox** is the point of this design. Enqueuing to Celery
inside a request handler has two failure modes with no clean fix: a crash between
`COMMIT` and `enqueue` silently drops the notification, and a rollback after a
successful enqueue sends a message about a change that never happened. Writing
the outbox row inside the same transaction as the domain change makes both
structurally impossible.

The cost is up to 30 seconds of latency on non-urgent notifications, which is
irrelevant for email and SMS.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    DeliveryStatus,
    NotificationChannel,
    NotificationEvent,
    OutboxStatus,
)
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type

# 1m, 5m, 25m, 2h, 10h — then dead-letter, visible in the admin delivery log.
MAX_DELIVERY_ATTEMPTS = 5


class NotificationOutbox(Base):
    """Domain events awaiting dispatch.

    BIGSERIAL rather than UUID: high-volume append-only, and the sweeper reads in
    insertion order.
    """

    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The sweeper's claim query. Partial, so the index stays small no matter
        # how many processed rows accumulate.
        Index(
            "ix_notification_outbox_claim",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
        ),
        Index(
            "ix_notification_outbox_entity",
            "entity_type",
            "entity_id",
            postgresql_where=text("entity_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    event: Mapped[NotificationEvent] = mapped_column(
        enum_type(NotificationEvent, "notification_event"), nullable=False
    )
    # Everything the dispatcher needs to render the message without re-reading
    # the domain: task name, project name, actor name, links.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # Deliberately not a foreign key: the outbox must survive deletion of the
    # entity that produced it, or a cascade would erase pending notifications.
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    status: Mapped[OutboxStatus] = mapped_column(
        enum_type(OutboxStatus, "outbox_status"), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationDelivery(Base):
    """One row per recipient per channel — the audit trail for "was it sent?".

    Skipped deliveries are recorded too (preference, opt-out, unverified phone,
    quiet hours). "No row" and "deliberately not sent" must be distinguishable
    when a manager asks why a worker never got the alert.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        # A row naming neither a user nor an address answers no question, so it is
        # not allowed to exist.
        CheckConstraint(
            "recipient_id IS NOT NULL OR destination IS NOT NULL",
            name="recipient_or_destination_present",
        ),
        Index("ix_notification_deliveries_recipient", "recipient_id", "created_at"),
        Index("ix_notification_deliveries_outbox", "outbox_id"),
        Index(
            "ix_notification_deliveries_provider",
            "provider_message_id",
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
        Index(
            "ix_notification_deliveries_failed",
            "status",
            "created_at",
            postgresql_where=text("status IN ('failed', 'bounced')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    outbox_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_outbox.id", ondelete="SET NULL"), nullable=True
    )
    # Nullable, because the auth flow mails people who do not have accounts yet:
    # an invitation and its OTP both precede the `users` row. Requiring a
    # recipient id would leave no delivery record for precisely the mail whose
    # failure is most costly — a bad address means the user never registers at
    # all. `destination` carries the address in that case.
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    event: Mapped[NotificationEvent] = mapped_column(
        enum_type(NotificationEvent, "notification_event"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        enum_type(NotificationChannel, "notification_channel"), nullable=False
    )

    # Snapshot of where it went. Kept even if the user later changes their email
    # or phone, so the record stays truthful.
    destination: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[DeliveryStatus] = mapped_column(
        enum_type(DeliveryStatus, "delivery_status"),
        nullable=False,
        default=DeliveryStatus.PENDING,
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InAppNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The in-app feed and its unread badge (FR-703).

    Read state syncs across a user's devices because it lives here rather than in
    the client.
    """

    __tablename__ = "in_app_notifications"
    __table_args__ = (
        # Drives the unread count and the feed's default ordering.
        Index(
            "ix_in_app_notifications_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_in_app_notifications_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[NotificationEvent] = mapped_column(
        enum_type(NotificationEvent, "notification_event"), nullable=False
    )

    # Rendered in the recipient's locale at dispatch time, so the feed does not
    # depend on the payload's shape surviving future changes.
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # In-app deep link, e.g. /projects/<id>/board?task=<id>
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
