"""Comments — the task activity feed (SPEC §6.7).

Threaded one level. Bodies are stored as Markdown and sanitized both on write
and on render — stored content is never trusted, because a sanitizer bug on the
write path would otherwise become permanent stored XSS.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.task import Task

# Comments may be edited within this window, after which they are immutable
# (FR-604) — the feed is part of the quality record.
EDIT_WINDOW_MINUTES = 15


class Comment(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "comments"
    __table_args__ = (
        Index(
            "ix_comments_task_created",
            "task_id",
            "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_comments_author", "author_id"),
        Index("ix_comments_search", "search_vector", postgresql_using="gin"),
        Index(
            "ix_comments_parent",
            "parent_comment_id",
            postgresql_where=text("parent_comment_id IS NOT NULL"),
        ),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    # One level of threading only; a reply to a reply attaches to the same root.
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Sanitized Markdown.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Denormalized from the body's @[Name](uuid) markers so notification
    # dispatch and "mentions of me" need no re-parse.
    mentioned_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )

    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Generated and maintained by PostgreSQL, so it can never drift from `body`.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(body, ''))", persisted=True),
        nullable=True,
    )

    task: Mapped[Task] = relationship(back_populates="comments", lazy="raise_on_sql")
    replies: Mapped[list[Comment]] = relationship(
        back_populates="parent", lazy="raise_on_sql", cascade="all, delete-orphan"
    )
    parent: Mapped[Comment | None] = relationship(
        back_populates="replies", lazy="raise_on_sql", remote_side="Comment.id"
    )
