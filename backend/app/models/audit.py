"""Append-only audit trail (SPEC §6.11, NFR-15).

Written by a service helper inside the same transaction as every mutation, so a
change cannot exist without its audit row.

The application's database role holds `INSERT` and `SELECT` on this table only —
no `UPDATE`, no `DELETE`. That grant is applied in the initial migration, which
means the trail cannot be rewritten by application code, *including buggy
application code*. That is the property worth having.

Foreign keys are deliberately absent from `entity_id` and `project_id`: the audit
row must outlive the row it describes, and a cascade would erase the history of
exactly the deletions most worth auditing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# SPEC §12.4. Partitioning by month is documented and deliberately deferred —
# premature at one line and under 50 workers (SPEC §14 R8).
RETENTION_MONTHS = 24


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor", "actor_id", "created_at"),
        Index("ix_audit_log_entity", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_log_action", "action", "created_at"),
        Index(
            "ix_audit_log_project",
            "project_id",
            "created_at",
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        # Retention enforcement scans by age.
        Index("ix_audit_log_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Nullable so system-initiated actions (the overdue scan, retention sweeps)
    # are recorded rather than skipped for lack of an actor.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # "task.cell_updated", "user.role_changed", "invitation.created", …
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    # Only the changed fields, not whole rows — a full snapshot of every task on
    # every keystroke would dwarf the data it describes.
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)

    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Correlates this row with the HTTP request and any Celery task it spawned.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
