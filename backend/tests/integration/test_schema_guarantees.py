"""The schema's promises, verified against real PostgreSQL.

These are the constraints the application relies on being true. A CHECK that
does not fire, or a cascade that does not cascade, is a data-integrity bug that
no amount of service-layer code can compensate for.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ColumnType, RoleKey
from app.models import AuditLog, BoardColumn, Comment, Task, User
from tests.factories import (
    make_column,
    make_group,
    make_project,
    make_task,
    make_user,
    unique_email,
)

pytestmark = pytest.mark.integration


# ══════════════════════════════════════════════════════════════════════════
#  identity
# ══════════════════════════════════════════════════════════════════════════
async def test_email_is_case_insensitive(db: AsyncSession) -> None:
    """CITEXT is what stops "Elad@x.com" becoming a second account."""
    email = unique_email("case")
    await make_user(db, email=email.lower())

    found = await db.scalar(select(User).where(User.email == email.upper()))
    assert found is not None


async def test_duplicate_email_differing_only_in_case_is_rejected(db: AsyncSession) -> None:
    email = unique_email("dupe")
    await make_user(db, email=email.lower())

    with pytest.raises(IntegrityError):
        await make_user(db, email=email.upper())


# ══════════════════════════════════════════════════════════════════════════
#  task constraints
# ══════════════════════════════════════════════════════════════════════════
async def test_due_date_before_start_date_is_rejected(db: AsyncSession) -> None:
    """This constraint caught a real bug in the seed script — keep it tested."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)

    with pytest.raises(IntegrityError):
        await make_task(
            db,
            project=project,
            created_by=user,
            start_date=date(2026, 7, 26),
            due_date=date(2026, 7, 22),
        )


async def test_task_cannot_be_its_own_parent(db: AsyncSession) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    task = await make_task(db, project=project, created_by=user)

    with pytest.raises(IntegrityError):
        await db.execute(
            text("UPDATE tasks SET parent_task_id = id WHERE id = :id"), {"id": task.id}
        )


async def test_priority_out_of_range_is_rejected(db: AsyncSession) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    task = await make_task(db, project=project, created_by=user)

    with pytest.raises(IntegrityError):
        await db.execute(text("UPDATE tasks SET priority = 9 WHERE id = :id"), {"id": task.id})


async def test_deleting_a_task_cascades_to_subtasks(db: AsyncSession) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    parent = await make_task(db, project=project, created_by=user, name="parent")
    await make_task(db, project=project, created_by=user, name="child", parent=parent)

    await db.delete(parent)
    await db.flush()

    remaining = await db.scalar(
        select(func.count()).select_from(Task).where(Task.project_id == project.id)
    )
    assert remaining == 0


async def test_deleting_a_project_cascades_to_tasks_and_columns(db: AsyncSession) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    group = await make_group(db, project=project)
    await make_task(db, project=project, created_by=user, group=group)

    await db.delete(project)
    await db.flush()

    assert await db.scalar(select(func.count()).select_from(Task)) == 0
    assert await db.scalar(select(func.count()).select_from(BoardColumn)) == 0


async def test_deactivating_a_user_preserves_their_task_history(db: AsyncSession) -> None:
    """FR-206: a deactivated worker's contributions stay attributed.

    `created_by` is ON DELETE RESTRICT precisely so a user with history cannot be
    hard-deleted by accident.
    """
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    await make_task(db, project=project, created_by=user)

    with pytest.raises(IntegrityError):
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})


# ══════════════════════════════════════════════════════════════════════════
#  hybrid column storage
# ══════════════════════════════════════════════════════════════════════════
async def test_custom_jsonb_round_trips_every_value_shape(db: AsyncSession) -> None:
    """Cell values keep their type — a number stays a number, a list a list."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    await make_column(db, project=project, key="severity", column_type=ColumnType.DROPDOWN)

    custom = {
        "severity": "critical",
        "measured_temp": 4.5,
        "verified": True,
        "people": [str(user.id)],
        "timeline": {"from": "2026-07-01", "to": "2026-07-08"},
        "note": None,
    }
    task = await make_task(db, project=project, created_by=user, custom=custom)

    # refresh, not expire: expiring then touching an attribute triggers a
    # synchronous lazy load, which raises MissingGreenlet under the async engine.
    await db.refresh(task)

    assert task.custom == custom
    assert isinstance(task.custom["measured_temp"], float)
    assert task.custom["verified"] is True


async def test_custom_jsonb_is_queryable_by_containment(db: AsyncSession) -> None:
    """The GIN index exists to serve this query shape — board filters."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    await make_task(db, project=project, created_by=user, custom={"severity": "critical"})
    await make_task(
        db, project=project, created_by=user, name="minor", custom={"severity": "minor"}
    )

    matched = (
        await db.scalars(select(Task).where(Task.custom.contains({"severity": "critical"})))
    ).all()
    assert len(matched) == 1


async def test_column_key_is_unique_per_project_among_live_columns(
    db: AsyncSession,
) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    await make_column(db, project=project, key="station")

    with pytest.raises(IntegrityError):
        await make_column(db, project=project, key="station", position=3000)


async def test_soft_deleted_column_key_becomes_reusable(db: AsyncSession) -> None:
    """The uniqueness index is partial on `deleted_at IS NULL`, so retiring a
    column frees its key without losing the historical values."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    old = await make_column(db, project=project, key="station")

    old.deleted_at = datetime.now(UTC)
    await db.flush()

    replacement = await make_column(db, project=project, key="station", position=3000)
    assert replacement.id != old.id


async def test_done_option_keys_reads_the_settings_json(db: AsyncSession) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)

    column = await db.scalar(
        select(BoardColumn).where(BoardColumn.project_id == project.id, BoardColumn.key == "status")
    )
    assert column is not None
    assert column.done_option_keys() == {"done"}
    assert column.is_system is True


# ══════════════════════════════════════════════════════════════════════════
#  full-text search (generated columns)
# ══════════════════════════════════════════════════════════════════════════
async def test_task_search_vector_is_generated_and_matches_hebrew(
    db: AsyncSession,
) -> None:
    """Generated column, so it can never drift from `name`.

    Hebrew has no PostgreSQL stemmer, hence the 'simple' configuration — exact
    word matching, with trigrams carrying partial matches (SPEC §14 R4).
    """
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    await make_task(db, project=project, created_by=user, name="בדיקת ניקיון מסוע ראשי")

    hit = await db.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.search_vector.op("@@")(func.to_tsquery("simple", "ניקיון")))
    )
    assert hit == 1


async def test_comment_search_vector_updates_when_body_changes(db: AsyncSession) -> None:
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    task = await make_task(db, project=project, created_by=user)

    comment = Comment(task_id=task.id, author_id=user.id, body="original wording here")
    db.add(comment)
    await db.flush()

    comment.body = "completely different phrasing"
    await db.flush()

    matched = await db.scalar(
        select(func.count())
        .select_from(Comment)
        .where(Comment.search_vector.op("@@")(func.to_tsquery("simple", "phrasing")))
    )
    assert matched == 1


# ══════════════════════════════════════════════════════════════════════════
#  audit log: append-only
# ══════════════════════════════════════════════════════════════════════════
async def test_audit_log_rejects_update(db: AsyncSession) -> None:
    """NFR-15. Enforced by a trigger, so it holds even for the table owner and
    even for a buggy ORM call — which is the actual threat model."""
    db.add(AuditLog(action="test.probe", entity_type="probe"))
    await db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("UPDATE audit_log SET action = 'tampered'"))


async def test_audit_log_rejects_delete_without_maintenance_opt_in(
    db: AsyncSession,
) -> None:
    db.add(AuditLog(action="test.probe", entity_type="probe"))
    await db.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM audit_log"))


async def test_audit_log_allows_delete_during_maintenance(db: AsyncSession) -> None:
    """Retention enforcement must still be able to remove rows past 24 months
    (SPEC §12.4), so the guard has a deliberate, explicit opt-in."""
    db.add(AuditLog(action="test.probe", entity_type="probe"))
    await db.flush()

    await db.execute(text("SET LOCAL kavim.audit_maintenance = 'on'"))
    await db.execute(text("DELETE FROM audit_log WHERE action = 'test.probe'"))

    assert await db.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_audit_log_survives_deletion_of_the_entity_it_describes(
    db: AsyncSession,
) -> None:
    """`entity_id` is deliberately not a foreign key: the record of a deletion
    must outlive the deleted row."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)

    db.add(AuditLog(action="project.deleted", entity_type="project", entity_id=project.id))
    await db.flush()
    await db.delete(project)
    await db.flush()

    assert await db.scalar(select(func.count()).select_from(AuditLog)) == 1


# ══════════════════════════════════════════════════════════════════════════
#  reference data
# ══════════════════════════════════════════════════════════════════════════
async def test_seeded_roles_and_permissions_match_the_registry(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    from app.core.permissions import DEFAULT_ROLE_MATRIX, PERMISSIONS
    from app.models import Permission, Role, RolePermission

    assert await db.scalar(select(func.count()).select_from(Permission)) == len(PERMISSIONS)
    assert await db.scalar(select(func.count()).select_from(Role)) == len(RoleKey)

    # A worker must not end up able to manage permissions — that is the whole
    # point of the matrix.
    worker = await db.scalar(select(Role).where(Role.key == RoleKey.WORKER))
    assert worker is not None
    worker_keys = set(
        (
            await db.scalars(
                select(Permission.key)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == worker.id)
            )
        ).all()
    )
    assert worker_keys == set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])
    assert "user:manage_permissions" not in worker_keys
    assert "task:update:status" in worker_keys


async def test_seed_is_idempotent(db: AsyncSession, seeded_reference: dict[str, object]) -> None:
    """Re-running must not duplicate reference data — it runs on every deploy."""
    from app.core.permissions import PERMISSIONS
    from app.models import Permission
    from app.scripts.seed import seed_permissions, seed_roles

    permissions = await seed_permissions(db)
    await seed_roles(db, permissions)
    await db.flush()

    assert await db.scalar(select(func.count()).select_from(Permission)) == len(PERMISSIONS)


# ══════════════════════════════════════════════════════════════════════════
#  ordering
# ══════════════════════════════════════════════════════════════════════════
async def test_fractional_position_allows_insertion_without_rewriting_neighbours(
    db: AsyncSession,
) -> None:
    """The reason `position` is NUMERIC and not an integer sequence: dragging one
    row writes one row, not every row below it (SPEC §7.4)."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    group = await make_group(db, project=project)

    first = await make_task(
        db, project=project, created_by=user, group=group, name="first", position=1000
    )
    second = await make_task(
        db, project=project, created_by=user, group=group, name="second", position=2000
    )
    middle = await make_task(
        db, project=project, created_by=user, group=group, name="middle", position=1500
    )

    ordered = (
        await db.scalars(select(Task.name).where(Task.group_id == group.id).order_by(Task.position))
    ).all()
    assert list(ordered) == ["first", "middle", "second"]

    # Neighbours were untouched.
    assert float(first.position) == 1000
    assert float(second.position) == 2000
    assert float(middle.position) == 1500


async def test_position_precision_survives_repeated_halving(db: AsyncSession) -> None:
    """NUMERIC(20,10) gives ~10 decimal places, enough for roughly 30 successive
    insertions at the same slot before the weekly rebalance is needed."""
    user = await make_user(db)
    project = await make_project(db, created_by=user)
    group = await make_group(db, project=project)

    low, high = 1000.0, 2000.0
    for index in range(20):
        midpoint = (low + high) / 2
        await make_task(
            db,
            project=project,
            created_by=user,
            group=group,
            name=f"task-{index}",
            position=midpoint,
        )
        high = midpoint

    positions = (
        await db.scalars(
            select(Task.position).where(Task.group_id == group.id).order_by(Task.position)
        )
    ).all()
    # All distinct — no collisions from precision loss.
    assert len(set(positions)) == len(positions)


# ══════════════════════════════════════════════════════════════════════════
#  invitations
# ══════════════════════════════════════════════════════════════════════════
async def test_only_one_pending_invitation_per_email(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    from tests.factories import make_invitation

    inviter = await make_user(db, role=RoleKey.LINE_MANAGER)
    email = unique_email("invitee")
    await make_invitation(db, invited_by=inviter, email=email)

    with pytest.raises(IntegrityError):
        await make_invitation(db, invited_by=inviter, email=email)


async def test_consumed_invitation_frees_the_email_for_a_new_one(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The uniqueness index is partial on `status = 'pending'`, so history
    accumulates freely while only one invitation is ever live."""
    from app.core.enums import InvitationStatus
    from tests.factories import make_invitation

    inviter = await make_user(db, role=RoleKey.LINE_MANAGER)
    email = unique_email("invitee")
    first, _ = await make_invitation(db, invited_by=inviter, email=email)

    first.status = InvitationStatus.CONSUMED
    first.consumed_at = datetime.now(UTC)
    await db.flush()

    second, _ = await make_invitation(db, invited_by=inviter, email=email)
    assert second.id != first.id


async def test_invitation_token_is_not_recoverable_from_the_row(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Only the SHA-256 digest is stored, so a database dump yields no usable
    invitation link (SPEC §8.3)."""
    from app.core.security import hash_token
    from tests.factories import make_invitation

    inviter = await make_user(db, role=RoleKey.LINE_MANAGER)
    invitation, raw_token = await make_invitation(db, invited_by=inviter)

    assert raw_token not in invitation.token_hash
    assert invitation.token_hash == hash_token(raw_token)
    assert len(invitation.token_hash) == 64


# ══════════════════════════════════════════════════════════════════════════
#  timestamps
# ══════════════════════════════════════════════════════════════════════════
async def test_timestamps_are_timezone_aware_utc(db: AsyncSession) -> None:
    """NFR-08. A naive datetime here would make every DST boundary a bug."""
    user = await make_user(db)

    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset() == timedelta(0)
