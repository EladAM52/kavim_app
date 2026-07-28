"""Create the very first administrator, on a database that has none.

    docker compose ... run --rm backend python -m app.scripts.bootstrap_admin \
        you@example.com "Your Name"

**The one bootstrap gap this closes.** A production database starts with
reference data and no users at all. Every route into the system needs somebody
who is already in it:

* `POST /admin/invitations` needs a bearer token, so it needs an account.
* `app.scripts.invite` refuses to run in production on purpose — a shell that
  can mint invitations defeats the point of an invitation, which is that it
  proves a manager sent it — and it needs an existing admin to record as the
  inviter regardless.
* `seed.py` refuses demo users in production, correctly: seven accounts sharing
  a published password have no business on a server real people can reach.

So there has to be exactly one way in, and this is it.

**It disables itself.** The guard is the state of the database, not the value of
`APP_ENV`: if any active user already holds `user:manage_permissions`, this
refuses. That is what keeps it from becoming a permanent back door — after the
first run it cannot create a second administrator, and everyone else arrives
through the invitation flow like they are supposed to.

The password is read from a prompt, or from stdin with `--password-stdin`.
Never from an argument: arguments land in shell history, in `ps` output, and in
any terminal recording.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Locale, RoleKey, UserStatus
from app.core.logging import configure_logging
from app.core.security import hash_password, validate_password_strength
from app.core.time import utc_now
from app.models.role import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.modules.audit import service as audit

RULE = "─" * 72

# The capability that defines "an administrator exists". Not a role name: the
# matrix is editable at runtime (FR-203), so the role holding this today need
# not be the one that held it at seed time.
ADMIN_PERMISSION = "user:manage_permissions"


async def _has_an_administrator(db: AsyncSession) -> bool:
    """Is there already an active account that can administer permissions?

    Asked of the permission matrix rather than of the role name, because the
    matrix is editable at runtime (FR-203): the role that grants
    `user:manage_permissions` today may not be the one that granted it at seed
    time, and it is the capability that matters, not the label.
    """
    statement = (
        select(func.count())
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            User.status == UserStatus.ACTIVE,
            Permission.key == ADMIN_PERMISSION,
        )
    )
    return bool(await db.scalar(statement))


async def create_first_admin(
    db: AsyncSession, email: str, full_name: str, password: str, locale: Locale
) -> int:
    """The whole decision, on a caller-owned session. Returns a process exit code.

    Separate from `_create` so the tests can run it against the test database's
    session rather than one this module opened for itself — the guard below is
    the safety property of the script, and an untested guard is an assumption.
    """
    if await _has_an_administrator(db):
        print(  # noqa: T201
            "refusing: this installation already has an active administrator.\n"
            "Invite further users from the admin panel, which records who sent it.",
            file=sys.stderr,
        )
        return 1

    role = await db.scalar(select(Role).where(Role.key == RoleKey.SYSTEM_ADMIN))
    if role is None:
        print(  # noqa: T201
            "SYSTEM_ADMIN is not seeded — run: python -m app.scripts.seed --reference",
            file=sys.stderr,
        )
        return 1

    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # A deactivated or invited account with this address already exists.
        # Silently promoting it would be a surprising thing for a bootstrap
        # command to do, so it stops and lets a human decide.
        print(  # noqa: T201
            f"refusing: {email} already exists (status {existing.status.value})",
            file=sys.stderr,
        )
        return 1

    now = utc_now()
    user = User(
        email=email,
        full_name=full_name,
        locale=locale,
        status=UserStatus.ACTIVE,
        password_hash=hash_password(password),
        password_changed_at=now,
    )
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id, assigned_by=user.id))

    # Audited like any other mutation (CLAUDE.md rule 6). The actor is the new
    # account itself: there is nobody else it could be, and an account that
    # appeared with no trace at all is exactly what an auditor would ask about.
    await audit.write_audit(
        db,
        action=audit.USER_REGISTERED,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={"email": email, "role": RoleKey.SYSTEM_ADMIN.value, "via": "bootstrap_admin"},
    )
    await db.commit()

    print(RULE)  # noqa: T201
    print(f"  administrator created:  {email}")  # noqa: T201
    print("  sign in, then invite everyone else from the admin panel.")  # noqa: T201
    print(RULE)  # noqa: T201
    return 0


async def _create(email: str, full_name: str, password: str, locale: Locale) -> int:
    from app.core.database import get_sessionmaker

    async with get_sessionmaker()() as db:
        return await create_first_admin(db, email, full_name, password, locale)


def _read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("password: ")
        if password != getpass.getpass("repeat:   "):
            print("passwords do not match", file=sys.stderr)  # noqa: T201
            return None

    problems = validate_password_strength(password)
    if problems:
        print("password rejected: " + "; ".join(problems), file=sys.stderr)  # noqa: T201
        return None
    return password


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the first administrator on an installation that has none."
    )
    parser.add_argument("email", help="address of the administrator to create")
    parser.add_argument("full_name", help='display name, e.g. "Elad Amir"')
    parser.add_argument(
        "--locale",
        choices=[locale.value for locale in Locale],
        default=Locale.HE.value,
        help="interface language for this account (default: he)",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of prompting, for automation",
    )
    args = parser.parse_args(argv)

    configure_logging()

    password = _read_password(args.password_stdin)
    if password is None:
        return 1

    async def run() -> int:
        from app.core.database import dispose_engine

        try:
            return await _create(args.email, args.full_name, password, Locale(args.locale))
        finally:
            # Inside the same loop: asyncpg connections belong to the loop that
            # created them, and disposing from a second `asyncio.run` fails with
            # "'NoneType' object has no attribute 'send'".
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
