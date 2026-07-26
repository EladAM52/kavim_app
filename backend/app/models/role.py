"""Roles and permissions — layer 1 of the authorization model (SPEC §8.4).

The role → permission matrix is data, not code, because a manager must be able
to edit it in the admin panel (FR-203). `RoleKey` values are seeded and stable;
which permissions each role holds is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import RoleKey
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type

if TYPE_CHECKING:
    from app.models.user import User


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    key: Mapped[RoleKey] = mapped_column(
        enum_type(RoleKey, "role_key"), nullable=False, unique=True
    )
    # Bilingual labels: {"he": "מנהל קו", "en": "Line manager"}
    label_he: Mapped[str] = mapped_column(String(100), nullable=False)
    label_en: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ordering for the admin matrix; also "seniority" for escalation targets.
    rank: Mapped[int] = mapped_column(nullable=False, default=0)
    # System roles cannot be deleted, only relabelled and re-permissioned.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", back_populates="roles", lazy="raise_on_sql"
    )
    # `user_roles` carries two FKs to `users` (user_id and assigned_by), so the
    # join must be stated explicitly — otherwise SQLAlchemy cannot tell which
    # one links the role to its holders.
    users: Mapped[list[User]] = relationship(
        secondary="user_roles",
        back_populates="roles",
        lazy="raise_on_sql",
        primaryjoin="Role.id == UserRole.role_id",
        secondaryjoin="User.id == UserRole.user_id",
        viewonly=True,
    )


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A permission string, `resource:action[:qualifier]`.

    Rows are seeded from the registry in `core/permissions.py`. A route that
    requires a permission not present here fails closed, which is the intended
    direction (FR-209).
    """

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # Grouping for the admin UI: "project", "task", "user", "report".
    resource: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description_he: Mapped[str] = mapped_column(String(200), nullable=False)
    description_en: Mapped[str] = mapped_column(String(200), nullable=False)

    roles: Mapped[list[Role]] = relationship(
        secondary="role_permissions", back_populates="permissions", lazy="raise_on_sql"
    )


class RolePermission(Base):
    """Association table. Editing it invalidates the Redis permission cache for
    every affected user immediately — a revoked permission must not survive in
    cache (SPEC §8.4)."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(Base):
    """User ↔ role assignment.

    Modelled as many-to-many rather than a single column on `users` so a future
    "supervisor who is also an auditor" needs no schema change.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
