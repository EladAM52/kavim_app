"""Reading the audit log (FR-208).

Read-only by construction, not by convention: `audit_log` carries a database
trigger that refuses `UPDATE` outright and permits `DELETE` only under an explicit
`SET LOCAL kavim.audit_maintenance` the application never sets. There is no write
endpoint here because there could not be a working one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.admin import AuditRow
from app.schemas.common import Page, decode_cursor, encode_cursor

MAX_PAGE = 200


def _apply_cursor(statement: Select[Any], cursor: str | None) -> Select[Any]:
    """Keyed on `id`, and that choice is load-bearing.

    The obvious cursor is `created_at`, and it is wrong here. `created_at`
    defaults to PostgreSQL's `now()`, which is **transaction** time — so every
    audit row written by one request shares an identical timestamp. Registration
    writes two; a bulk administrative action writes many. A `created_at` cursor
    then either skips the rest of the tied group or serves it forever.

    `id` is a `BigInteger` sequence: a total order, a faithful proxy for insertion
    order, and served directly by the primary key index.
    """
    if cursor is None:
        return statement
    payload = decode_cursor(cursor)
    try:
        anchor = int(payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("That page cursor is not valid.") from exc
    return statement.where(AuditLog.id < anchor)


async def list_entries(
    db: AsyncSession,
    *,
    limit: int = 50,
    cursor: str | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditRow]:
    limit = max(1, min(limit, MAX_PAGE))

    statement = select(AuditLog)
    if actor_id is not None:
        statement = statement.where(AuditLog.actor_id == actor_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        statement = statement.where(AuditLog.entity_id == entity_id)
    if since is not None:
        statement = statement.where(AuditLog.created_at >= since)
    if until is not None:
        statement = statement.where(AuditLog.created_at <= until)

    statement = _apply_cursor(statement, cursor)
    statement = statement.order_by(AuditLog.id.desc()).limit(limit + 1)

    rows = list(await db.scalars(statement))
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Actor names in one query. The log is read by people, and a page of UUIDs is
    # not a record of who did what.
    names: dict[uuid.UUID, str] = {}
    actor_ids = {row.actor_id for row in rows if row.actor_id is not None}
    if actor_ids:
        for user_id, full_name in await db.execute(
            select(User.id, User.full_name).where(User.id.in_(actor_ids))
        ):
            names[user_id] = full_name

    items = [
        AuditRow(
            id=row.id,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=str(row.entity_id) if row.entity_id else None,
            actor_id=str(row.actor_id) if row.actor_id else None,
            actor_name=names.get(row.actor_id) if row.actor_id else None,
            project_id=str(row.project_id) if row.project_id else None,
            before=row.before,
            after=row.after,
            ip=str(row.ip) if row.ip else None,
            request_id=row.request_id,
            created_at=row.created_at,
        )
        for row in rows
    ]

    next_cursor = encode_cursor({"id": rows[-1].id}) if has_more and rows else None
    return Page[AuditRow](items=items, next_cursor=next_cursor)
