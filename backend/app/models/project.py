"""Projects (quality reviews), groups, membership, and saved views."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ProjectPermissionLevel, ProjectStatus
from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type

if TYPE_CHECKING:
    from app.models.column import BoardColumn
    from app.models.site import Line
    from app.models.task import Task


class Project(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One quality review cycle — the Monday.com "board" (SPEC §2.3)."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="end_date_after_start_date",
        ),
        Index(
            "ix_projects_line_status",
            "line_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lines.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        enum_type(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.ACTIVE
    )
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # A template is a project that is never worked in — only copied (FR-306).
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_from_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    line: Mapped[Line | None] = relationship(back_populates="projects", lazy="raise_on_sql")
    groups: Mapped[list[Group]] = relationship(
        back_populates="project", lazy="raise_on_sql", cascade="all, delete-orphan"
    )
    columns: Mapped[list[BoardColumn]] = relationship(
        back_populates="project", lazy="raise_on_sql", cascade="all, delete-orphan"
    )
    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", lazy="raise_on_sql", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="project", lazy="raise_on_sql", cascade="all, delete-orphan"
    )


class ProjectMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Layer 2 of authorization (SPEC §8.4).

    A Line Manager holding `task:update:any` globally still cannot touch a
    project they are not a member of — effective permission is the intersection.
    """

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_id_user_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_level: Mapped[ProjectPermissionLevel] = mapped_column(
        enum_type(ProjectPermissionLevel, "project_permission_level"),
        nullable=False,
        default=ProjectPermissionLevel.VIEWER,
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    project: Mapped[Project] = relationship(back_populates="members", lazy="raise_on_sql")


class Group(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A section within a project — "Shift A", "Packaging station"."""

    __tablename__ = "groups"
    __table_args__ = (
        Index(
            "ix_groups_project_position",
            "project_id",
            "position",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Hex colour for the group's board accent stripe.
    color: Mapped[str] = mapped_column(String(9), nullable=False, default="#0f766e")
    # Fractional index — see task.py for why.
    position: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    is_collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project: Mapped[Project] = relationship(back_populates="groups", lazy="raise_on_sql")
    tasks: Mapped[list[Task]] = relationship(back_populates="group", lazy="raise_on_sql")


class SavedView(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named filter/sort/visibility combination (FR-806).

    Private by default; a manager may share one with the project. The filter
    itself is JSONB because its shape follows the board's columns, which are
    themselves user-defined.
    """

    __tablename__ = "saved_views"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "owner_id", "name", name="uq_saved_views_project_id_owner_id_name"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # "table" | "cards" | "kanban"
    view_type: Mapped[str] = mapped_column(String(20), nullable=False, default="table")
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sort: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    hidden_column_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
