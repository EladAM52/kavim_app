"""Tasks and subtasks — the central table.

Hybrid column storage (SPEC §7.2, ADR-004):

* **Hot fields** — `status_key`, `owner_id`, `start_date`, `due_date`,
  `priority`, `position` — are real typed, indexed columns. These are what
  filtering, sorting, and roll-ups run against.
* **User-defined fields** live in a single `custom` JSONB column with one GIN
  index, described by `board_columns`.

One row per task, one GIN index for every custom column. The rejected
alternatives were pure EAV (7500 rows to render a 500-task board) and a column
per field (`ALTER TABLE` triggered by end users).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import DependencyType
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.project import Group, Project

# Task → subtask only. A subtask cannot itself have children; enforced in the
# service layer plus a test, because enforcing it in SQL would need a trigger.
MAX_TASK_DEPTH = 2


class Task(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("parent_task_id IS NULL OR parent_task_id <> id", name="no_self_parent"),
        CheckConstraint(
            "due_date IS NULL OR start_date IS NULL OR due_date >= start_date",
            name="due_date_after_start_date",
        ),
        CheckConstraint("priority BETWEEN 0 AND 4", name="priority_in_range"),
        CheckConstraint("version >= 1", name="version_positive"),
        # ── the main board read ───────────────────────────────────────────
        Index(
            "ix_tasks_board",
            "project_id",
            "group_id",
            "position",
            postgresql_where=text("deleted_at IS NULL AND parent_task_id IS NULL"),
        ),
        Index(
            "ix_tasks_project_status",
            "project_id",
            "status_key",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # "My tasks", sorted by due date, and the hourly overdue scan.
        Index(
            "ix_tasks_due_date",
            "due_date",
            postgresql_where=text("deleted_at IS NULL AND due_date IS NOT NULL"),
        ),
        Index(
            "ix_tasks_owner",
            "owner_id",
            postgresql_where=text("deleted_at IS NULL AND owner_id IS NOT NULL"),
        ),
        Index(
            "ix_tasks_parent",
            "parent_task_id",
            "position",
            postgresql_where=text("parent_task_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        # ── custom columns and search ─────────────────────────────────────
        # jsonb_path_ops is smaller and faster than the default for the
        # containment queries filters actually generate.
        Index(
            "ix_tasks_custom_gin",
            "custom",
            postgresql_using="gin",
            postgresql_ops={"custom": "jsonb_path_ops"},
        ),
        Index("ix_tasks_search", "search_vector", postgresql_using="gin"),
        # Trigram index for partial matches. Hebrew has no PostgreSQL stemmer,
        # so trigrams carry most of the search quality (SPEC §14 R4).
        Index(
            "ix_tasks_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("groups.id", ondelete="SET NULL"), nullable=True
    )
    # Self-FK gives the task → subtask hierarchy. CASCADE so deleting a task
    # takes its subtasks with it.
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(500), nullable=False)

    # ── hot fields: real columns ──────────────────────────────────────────
    # Matches an option key in the status column's settings.options.
    status_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 0 = none … 4 = critical.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # Fractional index: placing a task between neighbours at 2.0 and 3.0 writes
    # 2.5 — one row, not every row below it. Concurrent drags converge instead
    # of conflicting (SPEC §7.4).
    position: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)

    # ── user-defined columns ──────────────────────────────────────────────
    # Keyed by board_columns.key. Validated per column type on write.
    custom: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Optimistic concurrency. Incremented on every cell write; a stale If-Match
    # returns 409 with the current value rather than overwriting (FR-504).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # Generated column, maintained by PostgreSQL — no trigger, no application
    # code to forget. 'simple' rather than a language config because there is no
    # Hebrew stemmer.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(name, ''))", persisted=True),
        nullable=True,
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="tasks", lazy="raise_on_sql")
    group: Mapped[Group | None] = relationship(back_populates="tasks", lazy="raise_on_sql")
    # Self-referential: remote_side goes on the many-to-one side only, naming
    # the parent's primary key.
    subtasks: Mapped[list[Task]] = relationship(
        back_populates="parent", lazy="raise_on_sql", cascade="all, delete-orphan"
    )
    parent: Mapped[Task | None] = relationship(
        back_populates="subtasks", lazy="raise_on_sql", remote_side="Task.id"
    )
    assignees: Mapped[list[TaskAssignee]] = relationship(
        back_populates="task", lazy="raise_on_sql", cascade="all, delete-orphan"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="task", lazy="raise_on_sql", cascade="all, delete-orphan"
    )

    @property
    def is_subtask(self) -> bool:
        return self.parent_task_id is not None

    def cell(self, column_key: str) -> Any:
        """Read a user-defined cell value. Hot fields are attributes, not here."""
        return self.custom.get(column_key)


class TaskAssignee(Base):
    """Multi-assignee support (FR-403).

    Separate from `owner_id`: the owner is the single accountable person shown in
    the board's Owner column, while assignees are everyone doing the work.
    """

    __tablename__ = "task_assignees"
    __table_args__ = (Index("ix_task_assignees_user", "user_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    task: Mapped[Task] = relationship(back_populates="assignees", lazy="raise_on_sql")


class TaskCellHistory(Base):
    """Per-cell change log (FR-505).

    Append-only and written inside the same transaction as the cell update, so a
    value can never change without a corresponding history row.
    """

    __tablename__ = "task_cell_history"
    __table_args__ = (
        Index("ix_task_cell_history_task_column", "task_id", "column_key", "changed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    # Either a board_columns.key or a system field name.
    column_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # JSONB rather than text so a value keeps its type — a number stays a number,
    # a person list stays a list.
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class TaskDependency(Base):
    """ "Blocked by" links (FR-408). Cycles are rejected in the service layer."""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "depends_on_task_id", name="uq_task_dependencies_task_id_depends_on_task_id"
        ),
        CheckConstraint("task_id <> depends_on_task_id", name="no_self_dependency"),
        Index("ix_task_dependencies_depends_on", "depends_on_task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[DependencyType] = mapped_column(
        enum_type(DependencyType, "dependency_type"),
        nullable=False,
        default=DependencyType.BLOCKS,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
