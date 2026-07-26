"""Attachments — evidence photos and documents (SPEC §6.9).

Uploads are presigned direct-to-storage, so the row exists in `PENDING` before
the object does. A large plant-floor photo never passes through the API process.
Unconfirmed `PENDING` rows are swept by maintenance.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AttachmentStatus, ScanStatus
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type


class Attachment(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        # An attachment belongs to a task or a comment, not both and not neither.
        CheckConstraint(
            "(task_id IS NOT NULL AND comment_id IS NULL)"
            " OR (task_id IS NULL AND comment_id IS NOT NULL)",
            name="exactly_one_parent",
        ),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        Index(
            "ix_attachments_task",
            "task_id",
            postgresql_where=text("task_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "ix_attachments_comment",
            "comment_id",
            postgresql_where=text("comment_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        # Drives the sweep for uploads that were presigned but never confirmed.
        Index(
            "ix_attachments_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    comment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )

    # Object key in storage. Never built from the user-supplied filename.
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    # Display name only — shown to users, never used as a path.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Sniffed from content, not from the extension (SPEC §8.3).
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Populated for images; drives the responsive srcset so a phone never
    # downloads a 6 MB original.
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[AttachmentStatus] = mapped_column(
        enum_type(AttachmentStatus, "attachment_status"),
        nullable=False,
        default=AttachmentStatus.PENDING,
    )
    # Hook is defined and no-ops by default, so ClamAV can be inserted later
    # without touching call sites.
    scan_status: Mapped[ScanStatus] = mapped_column(
        enum_type(ScanStatus, "scan_status"), nullable=False, default=ScanStatus.SKIPPED
    )
    scan_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")
