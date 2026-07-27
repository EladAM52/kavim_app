"""What a re-seed does to a matrix somebody edited at runtime.

FR-203 lets an administrator change the role → permission matrix. `seed_roles`
also writes to that table. These two tests pin what happens when they disagree,
because the behaviour is asymmetric, it is deliberate, and without a test it
would look like a bug the first time someone noticed it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RoleKey
from app.modules.admin import roles as roles_mod
from app.scripts.seed import seed_permissions, seed_roles

pytestmark = pytest.mark.integration


async def test_reseeding_restores_a_permission_revoked_at_runtime(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The recovery path, and the hazard, are the same behaviour.

    Recovery: a SYSTEM_ADMIN stripped of `user:manage_permissions` is repaired by
    `python -m app.scripts.seed --reference`, because the seeded default for that
    role is every permission. That is what stops a bad matrix edit from needing a
    database shell.

    Hazard: a deploy that runs the seeder will undo a *deliberate* revocation the
    same way. Check the deployment scripts before relying on FR-203 in production.
    """
    roles = seeded_reference["roles"]
    worker = roles[RoleKey.WORKER]  # type: ignore[index]

    await roles_mod.replace_role_permissions(db, worker, ["task:read"])
    assert await roles_mod._current_keys(db, worker.id) == {"task:read"}

    permissions = await seed_permissions(db)
    await seed_roles(db, permissions)
    await db.flush()

    restored = await roles_mod._current_keys(db, worker.id)
    assert "comment:create" in restored, "a revoked default should come back"


async def test_reseeding_leaves_an_extra_permission_alone(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The other direction: the seeder only ever adds.

    It never computes `current - wanted`, so a permission granted beyond the
    defaults survives. Making it reconcile-and-delete would wipe every runtime
    matrix edit on every deploy, which is strictly worse than the ratchet.
    """
    roles = seeded_reference["roles"]
    worker = roles[RoleKey.WORKER]  # type: ignore[index]

    granted = sorted({*(await roles_mod._current_keys(db, worker.id)), "report:read"})
    await roles_mod.replace_role_permissions(db, worker, granted)

    permissions = await seed_permissions(db)
    await seed_roles(db, permissions)
    await db.flush()

    assert "report:read" in await roles_mod._current_keys(db, worker.id)
