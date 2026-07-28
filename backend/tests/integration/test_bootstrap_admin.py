"""The first-administrator bootstrap.

`bootstrap_admin` is the only way into a fresh production installation, which
makes it the only script that can create an account without an invitation. What
stops that from being a permanent back door is that it disables itself once an
administrator exists — so that is what these tests are about.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Locale, RoleKey, UserStatus
from app.core.security import hash_password
from app.core.time import utc_now
from app.models.audit import AuditLog
from app.models.role import Role, UserRole
from app.models.user import User
from app.scripts.bootstrap_admin import _has_an_administrator, create_first_admin

pytestmark = pytest.mark.anyio

# Every test here needs the roles and the permission matrix; `seeded_reference`
# builds them with the real seeder, so they cannot drift from production.
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("seeded_reference")]


async def test_an_empty_installation_has_no_administrator(db: AsyncSession) -> None:
    assert await _has_an_administrator(db) is False


async def test_it_creates_an_active_system_admin_who_can_sign_in(db: AsyncSession) -> None:

    exit_code = await create_first_admin(
        db, "founder@example.com", "מנהל ראשון", "a-long-enough-passphrase", Locale.HE
    )
    assert exit_code == 0

    user = await db.scalar(select(User).where(User.email == "founder@example.com"))
    assert user is not None
    assert user.status is UserStatus.ACTIVE
    # A password hash, not the password: the account has to be able to log in.
    assert user.password_hash and user.password_hash != "a-long-enough-passphrase"

    role = await db.scalar(
        select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)
    )
    assert role is not None and role.key is RoleKey.SYSTEM_ADMIN

    # Audited like any other mutation (CLAUDE.md rule 6), with itself as actor —
    # an account that appeared with no trace is what an auditor would ask about.
    entry = await db.scalar(select(AuditLog).where(AuditLog.entity_id == user.id))
    assert entry is not None
    assert (entry.after or {}).get("via") == "bootstrap_admin"


async def test_it_refuses_once_an_administrator_exists(db: AsyncSession) -> None:
    """The guard that keeps this from being a back door.

    Deliberately checked against the *permission*, not the role name: the matrix
    is editable at runtime, so asking "is there a SYSTEM_ADMIN" would answer the
    wrong question after an administrator renames or re-grants roles.
    """
    first = await create_first_admin(
        db, "first@example.com", "First", "a-long-enough-passphrase", Locale.HE
    )
    assert first == 0

    second = await create_first_admin(
        db, "second@example.com", "Second", "a-long-enough-passphrase", Locale.HE
    )

    assert second == 1
    assert await db.scalar(select(User).where(User.email == "second@example.com")) is None


async def test_it_refuses_an_address_that_already_exists(db: AsyncSession) -> None:
    """Even before any administrator exists.

    A deactivated account is the case that matters: reactivating somebody by
    running a bootstrap command — and silently handing them SYSTEM_ADMIN — is not
    something this script should be able to do by accident.
    """
    db.add(
        User(
            email="taken@example.com",
            full_name="Departed",
            status=UserStatus.DEACTIVATED,
            # A password-auth row must carry a hash; the schema has a CHECK.
            password_hash=hash_password("a-long-enough-passphrase"),
            password_changed_at=utc_now(),
        )
    )
    await db.commit()

    assert (
        await create_first_admin(
            db, "taken@example.com", "Taken", "a-long-enough-passphrase", Locale.HE
        )
        == 1
    )
