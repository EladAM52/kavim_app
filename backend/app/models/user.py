"""Users and their notification preferences."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AuthProvider,
    Locale,
    NotificationChannel,
    NotificationEvent,
    UserStatus,
)
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type

if TYPE_CHECKING:
    from app.models.role import Role


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(auth_provider = 'password' AND password_hash IS NOT NULL)"
            " OR (auth_provider <> 'password')",
            name="password_users_have_hash",
        ),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        CheckConstraint("digest_hour BETWEEN 0 AND 23", name="digest_hour_in_range"),
        # Partial: deleted users are never listed, so they do not belong in the index.
        Index("ix_users_status", "status", postgresql_where=text("deleted_at IS NULL")),
    )

    # CITEXT so "Elad@x.com" and "elad@x.com" can never become two accounts.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # E.164, e.g. +972501234567. SMS is only ever sent to a verified number.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sms_opted_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    locale: Mapped[Locale] = mapped_column(
        enum_type(Locale, "locale"), nullable=False, default=Locale.HE
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Jerusalem")

    status: Mapped[UserStatus] = mapped_column(
        enum_type(UserStatus, "user_status"), nullable=False, default=UserStatus.INVITED
    )

    # ── authentication ────────────────────────────────────────────────────
    auth_provider: Mapped[AuthProvider] = mapped_column(
        enum_type(AuthProvider, "auth_provider"), nullable=False, default=AuthProvider.PASSWORD
    )
    # argon2id. Null only for SSO-provisioned accounts.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Reserved so Entra ID becomes an added path, not a migration (SPEC §14 R7).
    external_idp_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    # Lockout after repeated failures (FR-109).
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # ── digest ────────────────────────────────────────────────────────────
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Local hour in the user's timezone, 0-23.
    digest_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=7)
    # Local wall-clock times in the user's timezone. A window that wraps
    # midnight (22:00 → 06:00) is valid and handled in the service layer.
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)

    # Explicit join: `user_roles` has two FKs to `users` (user_id, assigned_by).
    # viewonly because assignment goes through UserRole rows, which carry
    # `assigned_by` and `assigned_at` that a plain association write would drop.
    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles",
        back_populates="users",
        lazy="raise_on_sql",
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        viewonly=True,
    )
    notification_preferences: Mapped[list[NotificationPreference]] = relationship(
        back_populates="user", lazy="raise_on_sql", cascade="all, delete-orphan"
    )

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def can_receive_sms(self) -> bool:
        """SMS requires a verified number and no opt-out (FR-702, FR-711)."""
        return (
            self.phone is not None
            and self.phone_verified_at is not None
            and self.sms_opted_out_at is None
        )


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per user per event per channel (FR-705).

    Absence of a row means "use the default for this event", so seeding every
    combination is unnecessary — only explicit choices are stored.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "event", "channel", name="uq_notification_preferences_user_event_channel"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[NotificationEvent] = mapped_column(
        enum_type(NotificationEvent, "notification_event"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        enum_type(NotificationChannel, "notification_channel"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(
        back_populates="notification_preferences", lazy="raise_on_sql"
    )
