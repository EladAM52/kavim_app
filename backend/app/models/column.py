"""Board column definitions — the column engine.

This table is what makes the product Monday-like: a manager adds a column and it
is an `INSERT`, never a migration and never `ALTER TABLE` (SPEC §6.5, §7.2).

`key` is the stable identifier used inside `tasks.custom`, so renaming a column's
label never touches stored cell values.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ColumnType
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type

if TYPE_CHECKING:
    from app.models.project import Project


class BoardColumn(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "board_columns"
    __table_args__ = (
        # `key` is unique per project among live columns. A soft-deleted column's
        # key becomes reusable once the 30-day retention window passes.
        Index(
            "uq_board_columns_project_key",
            "project_id",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_board_columns_project_position",
            "project_id",
            "position",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("width >= 60 AND width <= 800", name="width_in_range"),
        UniqueConstraint("project_id", "system_field", name="uq_board_columns_project_system"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    # Stable identifier used as the JSONB key in `tasks.custom`. snake_case.
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[ColumnType] = mapped_column(enum_type(ColumnType, "column_type"), nullable=False)

    label_he: Mapped[str] = mapped_column(String(120), nullable=False)
    label_en: Mapped[str] = mapped_column(String(120), nullable=False)

    # Type-specific configuration, validated per type in
    # modules/projects/columns.py. For a status column: the option list with
    # labels, colours, and which options count as "done".
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Fractional index so reordering writes one row (see task.py).
    position: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    width: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=160)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── layer 3 of authorization (SPEC §8.4) ──────────────────────────────
    # Which global roles may write this column. Empty means "anyone with
    # task:update on the project". This is the mechanism that lets a worker
    # update Status and Due date while only a manager edits Root cause.
    editable_by_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── system columns ────────────────────────────────────────────────────
    # Non-null when this definition describes a real column on `tasks` rather
    # than a JSONB key: 'status_key', 'owner_id', 'start_date', 'due_date',
    # 'priority'. System columns can be relabelled and recoloured but never
    # deleted (SPEC §7.3).
    system_field: Mapped[str | None] = mapped_column(String(32), nullable=True)

    project: Mapped[Project] = relationship(back_populates="columns", lazy="raise_on_sql")

    @property
    def is_system(self) -> bool:
        return self.system_field is not None

    @property
    def status_options(self) -> list[dict[str, Any]]:
        """Option list for a status or dropdown column, or empty for others."""
        options = self.settings.get("options", [])
        return options if isinstance(options, list) else []

    def done_option_keys(self) -> set[str]:
        """Options that count as complete — drives completion metrics and the
        overdue scan (FR-304)."""
        return {
            str(option["key"])
            for option in self.status_options
            if isinstance(option, dict) and option.get("is_done") and "key" in option
        }
