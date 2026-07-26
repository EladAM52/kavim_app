"""Sites and production lines.

Present from Phase 1 even though the UI does not expose them yet. Every project
carries a `line_id`, so growing from one line to several is additive rather than
a migration of existing rows (SPEC §2.3).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.project import Project


class Site(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "sites"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Jerusalem")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[Line]] = relationship(
        back_populates="site", lazy="raise_on_sql", cascade="all, delete-orphan"
    )


class Line(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "lines"
    __table_args__ = (UniqueConstraint("site_id", "code", name="uq_lines_site_id_code"),)

    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    site: Mapped[Site] = relationship(back_populates="lines", lazy="raise_on_sql")
    projects: Mapped[list[Project]] = relationship(back_populates="line", lazy="raise_on_sql")
