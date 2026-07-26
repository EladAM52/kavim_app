"""Authentication artefacts: invitations, OTP codes, refresh tokens, resets.

Every secret in this module is stored **hashed**. A database dump therefore
yields no usable invitation link, OTP code, or session token (SPEC §8.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import InvitationStatus, OtpChannel, OtpPurpose, TokenRevokeReason
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type


class Invitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single-use, 7-day invitation (FR-101, FR-102).

    The registration email is taken from this row, never from the form the
    invitee fills in — that is what prevents an invitation being forwarded and
    redeemed by a different address (SPEC §8.1).
    """

    __tablename__ = "invitations"
    __table_args__ = (
        # One live invitation per address; consumed and revoked rows accumulate
        # freely as history.
        Index(
            "uq_invitations_pending_email",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_invitations_status_expires", "status", "expires_at"),
    )

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    # SHA-256 of the raw token. The token itself is never persisted.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    # Projects the invitee is added to on registration.
    project_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )

    invited_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[InvitationStatus] = mapped_column(
        enum_type(InvitationStatus, "invitation_status"),
        nullable=False,
        default=InvitationStatus.PENDING,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Superseded when a manager resends (FR-111).
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invitations.id", ondelete="SET NULL"), nullable=True
    )


class OtpCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A 6-digit one-time code, hashed at rest (FR-104).

    Sent to the address on the *invitation*, not one the user types, which is
    what makes it proof of mailbox control rather than a formality.
    """

    __tablename__ = "otp_codes"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        Index("ix_otp_codes_lookup", "email", "purpose", "created_at"),
    )

    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    # SHA-256 of the code. Compared in constant time.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    purpose: Mapped[OtpPurpose] = mapped_column(
        enum_type(OtpPurpose, "otp_purpose"), nullable=False
    )
    channel: Mapped[OtpChannel] = mapped_column(
        enum_type(OtpChannel, "otp_channel"), nullable=False, default=OtpChannel.EMAIL
    )
    # Set for phone verification, where the code goes to a number not an address.
    destination: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_ip: Mapped[str | None] = mapped_column(INET, nullable=True)


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """A rotating refresh token with reuse detection (SPEC §8.2).

    Every refresh issues a new token and marks its parent `rotated`. Presenting
    an already-rotated token means one was stolen and replayed, so the entire
    `family_id` is revoked at once — turning theft into a single-use event rather
    than persistent access.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_active", "user_id", "expires_at"),
        Index(
            "ix_refresh_tokens_family_active",
            "family_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Shared by every token in one rotation chain.
    family_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[TokenRevokeReason | None] = mapped_column(
        enum_type(TokenRevokeReason, "token_revoke_reason"), nullable=True
    )


class PasswordResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use, 1-hour password reset (FR-108).

    A successful reset revokes every refresh token for the user — if the reset
    was triggered because an account was compromised, leaving old sessions alive
    would defeat the point.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_user", "user_id", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
