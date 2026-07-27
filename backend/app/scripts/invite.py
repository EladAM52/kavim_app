"""Create an invitation from the command line, and read back the OTP.

    uv run python -m app.scripts.invite worker@example.com
    uv run python -m app.scripts.invite worker@example.com --role LINE_MANAGER
    uv run python -m app.scripts.invite --sweep

**A convenience wrapper around `POST /admin/invitations`**, which exists as of
Phase 3. It calls the same `auth.service.invite_user` the endpoint does, so the
two cannot drift; what it adds is not needing a bearer token, and `--sweep`.

`--sweep` runs one outbox dispatch and prints any verification code it sent, so the
flow is completable while `EMAIL_DRY_RUN=true` and no mail actually leaves. That is
the whole development loop:

    1. python -m app.scripts.invite worker@example.com   → open the printed URL
    2. click through to the code screen in the browser
    3. python -m app.scripts.invite --sweep              → copy the code

Refuses to run in production. The point of an invitation is that it proves a
manager sent it; a shell that can mint them bypasses that, which is fine on a
laptop and not fine on a server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.core.config import settings
from app.core.enums import Locale, RoleKey
from app.core.logging import configure_logging
from app.core.time import to_local
from app.integrations.smtp_client import get_email_sender
from app.models.notification import NotificationOutbox
from app.models.role import Role
from app.models.user import User
from app.modules.auth.invitations import registration_url
from app.modules.auth.service import invite_user
from app.modules.notifications import outbox

# Any active admin will do as the inviter; the row needs a real `invited_by`.
_INVITER_ROLES = (RoleKey.SYSTEM_ADMIN, RoleKey.LINE_MANAGER)

RULE = "─" * 72


async def _create(email: str, role_key: RoleKey) -> int:
    from app.core.database import get_sessionmaker

    async with get_sessionmaker()() as db:
        role = await db.scalar(select(Role).where(Role.key == role_key))
        if role is None:
            print(f"role {role_key.value} is not seeded — run: python -m app.scripts.seed")  # noqa: T201
            return 1

        inviter = await db.scalar(
            select(User)
            .join(Role, Role.id.isnot(None))
            .where(User.email == "admin@kavim.example.com")
            .limit(1)
        )
        if inviter is None:
            # Fall back to any admin-ish account, so a re-seeded database with
            # different demo addresses still works.
            inviter = await db.scalar(
                select(User).where(User.deleted_at.is_(None)).order_by(User.created_at).limit(1)
            )
        if inviter is None:
            print("no users exist — run: python -m app.scripts.seed")  # noqa: T201
            return 1

        # The same call `POST /admin/invitations` makes. Since Phase 3 that
        # endpoint exists, so this script is a convenience rather than the only
        # way in — and routing both through one function is what stops the shell
        # path and the API path from becoming two different invitation flows.
        invitation, raw_token = await invite_user(
            db,
            email=email,
            role_id=role.id,
            invited_by=inviter.id,
            locale=Locale(settings.DEFAULT_LOCALE),
        )
        await db.commit()

        print(RULE)  # noqa: T201
        print(f"  invited     {email}")  # noqa: T201
        print(f"  role        {role_key.value}")  # noqa: T201
        print(f"  by          {inviter.full_name} <{inviter.email}>")  # noqa: T201
        print(f"  expires     {to_local(invitation.expires_at):%d/%m/%Y %H:%M} Asia/Jerusalem")  # noqa: T201
        print(RULE)  # noqa: T201
        print("  Open this link:")  # noqa: T201
        print(f"  {registration_url(raw_token)}")  # noqa: T201
        print(RULE)  # noqa: T201
        print("  Then, to get the verification code:")  # noqa: T201
        print("    uv run python -m app.scripts.invite --sweep")  # noqa: T201
        print(RULE)  # noqa: T201

    return 0


async def _sweep() -> int:
    """Dispatch queued mail and print any code it carried.

    Reads the code out of the outbox payload rather than the log, because the
    dry-run log line is one long structured record and hunting six digits out of it
    on a Windows console is miserable.
    """
    from app.core.database import get_sessionmaker

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db:
        result = await outbox.sweep(db, sender=get_email_sender())
        await db.commit()

    print(RULE)  # noqa: T201
    print(  # noqa: T201
        f"  swept       claimed={result.claimed} sent={result.sent} "
        f"deferred={result.deferred} failed={result.failed} dead={result.dead_lettered}"
    )

    if result.sent == 0:
        print("  nothing was dispatched — request a code in the browser first")  # noqa: T201
        print(RULE)  # noqa: T201
        return 0

    async with sessionmaker() as db:
        rows = await db.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.event.in_(["otp_code", "invitation", "password_reset"]))
            .order_by(NotificationOutbox.id.desc())
            .limit(result.sent)
        )
        for row in rows:
            context = (row.payload or {}).get("context") or {}
            recipient = (row.payload or {}).get("to_email")
            if code := context.get("code"):
                print(f"  code        {code}   → {recipient}")  # noqa: T201
            elif url := context.get("reset_url") or context.get("registration_url"):
                print(f"  link        {url}   → {recipient}")  # noqa: T201

    print(RULE)  # noqa: T201
    return 0


async def _create_json(email: str, role_key: RoleKey) -> int:
    """`--json`: the same invitation, as one line of machine-readable output.

    This is the seam Playwright uses. An end-to-end test has to *start* from a
    real invitation, and the alternatives are worse: a second minting path in the
    test harness would drift from the real one, and reaching into PostgreSQL from
    Node means a second database driver in the frontend toolchain.

    Nothing test-only is added to the application — this reuses `invite_user`,
    which is the same function `POST /admin/invitations` calls.
    """
    from app.core.database import get_sessionmaker

    async with get_sessionmaker()() as db:
        role = await db.scalar(select(Role).where(Role.key == role_key))
        inviter = await db.scalar(
            select(User).where(User.deleted_at.is_(None)).order_by(User.created_at).limit(1)
        )
        if role is None or inviter is None:
            print(json.dumps({"error": "not seeded"}), file=sys.stderr)  # noqa: T201
            return 1

        invitation, raw_token = await invite_user(
            db,
            email=email,
            role_id=role.id,
            invited_by=inviter.id,
            locale=Locale(settings.DEFAULT_LOCALE),
        )
        await db.commit()

        print(  # noqa: T201
            json.dumps(
                {
                    "id": str(invitation.id),
                    "email": email,
                    "token": raw_token,
                    "url": registration_url(raw_token),
                }
            )
        )
    return 0


async def _latest_otp(email: str) -> int:
    """`--otp EMAIL`: the code most recently *queued* for an address.

    Read from the outbox payload rather than `otp_codes`, because only the digest
    is stored there — the plaintext exists exactly once, in the message. Reading
    it back is what lets a test complete the flow without a mailbox.

    Deliberately does not sweep. A test that wants the mail actually sent calls
    `--sweep` itself, so the two steps stay separable and a test can assert on
    the queued state before dispatch.
    """
    from app.core.database import get_sessionmaker

    async with get_sessionmaker()() as db:
        rows = await db.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.event == "otp_code")
            .order_by(NotificationOutbox.id.desc())
            .limit(20)
        )
        for row in rows:
            payload = row.payload or {}
            if str(payload.get("to_email", "")).lower() != email.lower():
                continue
            code = (payload.get("context") or {}).get("code")
            if code:
                print(json.dumps({"email": email, "code": code}))  # noqa: T201
                return 0

    print(json.dumps({"error": f"no queued otp for {email}"}), file=sys.stderr)  # noqa: T201
    return 1


async def _run(args: argparse.Namespace) -> int:
    from app.core.database import dispose_engine

    try:
        if args.otp:
            return await _latest_otp(args.otp)
        if args.sweep:
            return await _sweep()
        if args.json:
            return await _create_json(args.email, RoleKey(args.role))
        return await _create(args.email, RoleKey(args.role))
    finally:
        # Inside the same loop: asyncpg connections belong to the loop that created
        # them, so disposing in a second `asyncio.run` fails.
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create an invitation, or dispatch queued mail and show the code.",
    )
    parser.add_argument("email", nargs="?", help="address to invite")
    parser.add_argument(
        "--role",
        default=RoleKey.WORKER.value,
        choices=[role.value for role in RoleKey],
        help="role the invitee is assigned (default: WORKER)",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="dispatch queued notifications and print any verification code",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the invitation as one line of JSON (used by the e2e suite)",
    )
    parser.add_argument(
        "--otp",
        metavar="EMAIL",
        help="print the verification code most recently queued for an address",
    )
    args = parser.parse_args(argv)

    if not (args.sweep or args.otp) and not args.email:
        parser.error("provide an email address, or --sweep, or --otp EMAIL")

    # Hebrew project names reach stdout via the sweeper's logging, and a Windows
    # console defaults to cp1252 — which kills the run partway with
    # UnicodeEncodeError after the work is already done.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    configure_logging()

    if settings.is_production:
        print("refusing to mint invitations from a shell in production", file=sys.stderr)  # noqa: T201
        return 1

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
