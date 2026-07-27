"""The auth flow and its security properties (SPEC §8.1, §8.2, §8.3).

These assert *behaviour under attack*, not just the happy path. The happy path
gets one test; the rest are the properties that make the flow safe, each written
so it fails if the property is removed:

* an invitation cannot be redeemed twice, or after expiry
* the account's email comes from the invitation, not the form
* a replayed refresh token kills the whole family
* an unknown email is indistinguishable from a known one
* ten failed logins lock the account, and the lock is audited
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InvitationStatus, RoleKey, UserStatus
from app.core.security import decode_access_token, hash_otp
from app.core.time import utc_now
from app.models.audit import AuditLog
from app.models.auth import OtpCode, RefreshToken
from app.models.notification import NotificationOutbox
from app.models.user import User
from app.modules.auth.router import REFRESH_COOKIE
from tests.factories import TEST_PASSWORD, make_invitation, make_user, unique_email

pytestmark = pytest.mark.integration

API = "/api/v1/auth"


# ══════════════════════════════════════════════════════════════════════════
#  helpers
# ══════════════════════════════════════════════════════════════════════════
async def _latest_otp(db: AsyncSession, email: str) -> OtpCode:
    code = await db.scalar(
        select(OtpCode)
        .where(OtpCode.email == email, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    assert code is not None, "no OTP row was written"
    return code


async def _outbox_payload(db: AsyncSession, event_value: str) -> dict[str, Any]:
    row = await db.scalar(
        select(NotificationOutbox)
        .where(NotificationOutbox.event == event_value)
        .order_by(NotificationOutbox.id.desc())
        .limit(1)
    )
    assert row is not None, f"no outbox row for {event_value}"
    return dict(row.payload)


async def _plaintext_code_from_outbox(db: AsyncSession, email: str) -> str:
    """Recover the code the user would have received.

    The database stores only a hash, so the plaintext is read back out of the
    queued outbox payload — which is exactly where the worker would find it.
    """
    payload = await _outbox_payload(db, "otp_code")
    assert payload["to_email"] == email, "the code was queued to the wrong address"
    code: str = payload["context"]["code"]
    return code


async def _audit_actions(db: AsyncSession) -> list[str]:
    rows = await db.scalars(select(AuditLog.action).order_by(AuditLog.id))
    return [str(action) for action in rows]


async def _reach_registration_ticket(
    api: AsyncClient, db: AsyncSession, raw_token: str, email: str
) -> str:
    otp_response = await api.post(f"{API}/otp/request", json={"token": raw_token})
    assert otp_response.status_code == 202

    code = await _plaintext_code_from_outbox(db, email)

    verify = await api.post(f"{API}/otp/verify", json={"token": raw_token, "code": code})
    assert verify.status_code == 200, verify.text
    ticket: str = verify.json()["registration_ticket"]
    return ticket


# ══════════════════════════════════════════════════════════════════════════
#  happy path
# ══════════════════════════════════════════════════════════════════════════
async def test_full_onboarding_flow(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """invitation → OTP → register → login → refresh → logout, end to end."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin, role=RoleKey.WORKER)

    preview = await api.get(f"{API}/invitations/{raw_token}")
    assert preview.status_code == 200
    assert preview.json()["email"] == invitation.email
    assert preview.json()["invited_by_name"] == admin.full_name

    ticket = await _reach_registration_ticket(api, db, raw_token, invitation.email)

    registered = await api.post(
        f"{API}/register",
        json={
            "registration_ticket": ticket,
            "full_name": "עובד חדש",
            "password": "a-long-enough-passphrase",
            "phone": "050-123-4567",
            "locale": "he",
        },
    )
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["user"]["email"] == invitation.email
    assert body["user"]["roles"] == [RoleKey.WORKER.value]
    # The worker role carries real permissions, so the identity is usable for
    # UI gating straight away.
    assert "task:update:status" in body["user"]["permissions"]
    assert REFRESH_COOKIE in registered.cookies

    # The access token is a usable, correctly scoped JWT.
    claims = decode_access_token(body["access_token"])
    assert claims["email"] == invitation.email

    # Phone was normalized to E.164 on the way in.
    user = await db.scalar(select(User).where(User.email == invitation.email))
    assert user is not None
    assert user.phone == "+972501234567"
    assert user.status is UserStatus.ACTIVE

    await db.refresh(invitation)
    assert invitation.status is InvitationStatus.CONSUMED
    assert invitation.consumed_by == user.id

    login = await api.post(
        f"{API}/login",
        json={"email": invitation.email, "password": "a-long-enough-passphrase"},
    )
    assert login.status_code == 200

    refreshed = await api.post(f"{API}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != login.json()["access_token"]

    logout = await api.post(f"{API}/logout")
    assert logout.status_code == 200

    actions = await _audit_actions(db)
    for expected in ("user.registered", "invitation.consumed", "auth.login_succeeded"):
        assert expected in actions, f"{expected} was not audited"


# ══════════════════════════════════════════════════════════════════════════
#  invitation validity — FR-102
# ══════════════════════════════════════════════════════════════════════════
async def test_expired_invitation_is_gone(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    invitation.expires_at = utc_now() - timedelta(minutes=1)
    await db.flush()

    response = await api.get(f"{API}/invitations/{raw_token}")
    assert response.status_code == 410
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "gone"


async def test_consumed_invitation_is_gone(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    _, raw_token = await make_invitation(db, invited_by=admin, status=InvitationStatus.CONSUMED)

    response = await api.get(f"{API}/invitations/{raw_token}")
    assert response.status_code == 410


async def test_unknown_invitation_token_is_not_found(api: AsyncClient) -> None:
    response = await api.get(f"{API}/invitations/{'z' * 43}")
    assert response.status_code == 404


async def test_an_invitation_cannot_be_registered_twice(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The single-use property, exercised through the API rather than asserted
    on the row: a second registration with the same ticket must fail."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    ticket = await _reach_registration_ticket(api, db, raw_token, invitation.email)

    payload = {
        "registration_ticket": ticket,
        "full_name": "First Redeemer",
        "password": "a-long-enough-passphrase",
    }
    assert (await api.post(f"{API}/register", json=payload)).status_code == 201

    second = await api.post(f"{API}/register", json={**payload, "full_name": "Second Redeemer"})
    assert second.status_code == 410


# ══════════════════════════════════════════════════════════════════════════
#  the email comes from the invitation — SPEC §8.1
# ══════════════════════════════════════════════════════════════════════════
async def test_registration_ignores_any_email_in_the_request(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """`RegisterRequest` has no email field, and `extra="forbid"` means smuggling
    one in is a 422 rather than being silently ignored. That is the stronger
    guarantee: a field that is quietly dropped invites someone to add handling
    for it later."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    ticket = await _reach_registration_ticket(api, db, raw_token, invitation.email)

    response = await api.post(
        f"{API}/register",
        json={
            "registration_ticket": ticket,
            "full_name": "Attacker",
            "password": "a-long-enough-passphrase",
            "email": "attacker@evil.test",
        },
    )
    assert response.status_code == 422

    # And nothing was created under the injected address.
    assert await db.scalar(select(User).where(User.email == "attacker@evil.test")) is None


async def test_otp_is_queued_to_the_invited_address_only(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)

    assert (await api.post(f"{API}/otp/request", json={"token": raw_token})).status_code == 202

    payload = await _outbox_payload(db, "otp_code")
    assert payload["to_email"] == invitation.email
    assert payload["channel"] == "email"

    # The stored row keeps only a hash of the code that was queued.
    code_row = await _latest_otp(db, invitation.email)
    assert code_row.code_hash == hash_otp(payload["context"]["code"])
    assert payload["context"]["code"] not in code_row.code_hash


# ══════════════════════════════════════════════════════════════════════════
#  OTP attempt budget — SPEC §8.3
# ══════════════════════════════════════════════════════════════════════════
async def test_otp_attempts_are_counted_in_the_database(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The database counter is the guarantee, not the Redis limiter — so this
    test runs with limiting effectively disabled and still must stop."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    # Held as a plain string: an error response rolls back and expires the ORM
    # instance — see the `api` fixture docstring.
    email = invitation.email
    await api.post(f"{API}/otp/request", json={"token": raw_token})

    code_row = await _latest_otp(db, email)
    budget = code_row.max_attempts
    correct = await _plaintext_code_from_outbox(db, email)

    for _ in range(budget):
        wrong = await api.post(f"{API}/otp/verify", json={"token": raw_token, "code": "000000"})
        assert wrong.status_code == 400

    # The increments survived the error responses, which is the whole point: under
    # a naive `get_db` they would have been rolled back by the very 400 that
    # recorded them, and the code would be guessable without limit.
    refreshed = await _latest_otp(db, email)
    assert refreshed.attempts == budget

    # Budget spent: even the correct code is now refused.
    exhausted = await api.post(f"{API}/otp/verify", json={"token": raw_token, "code": correct})
    assert exhausted.status_code == 429


async def test_requesting_a_new_code_expires_the_previous_one(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Several live codes would multiply an attacker's chances per guess."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    email = invitation.email

    await api.post(f"{API}/otp/request", json={"token": raw_token})
    first_code = await _plaintext_code_from_outbox(db, email)

    await api.post(f"{API}/otp/request", json={"token": raw_token})

    stale = await api.post(f"{API}/otp/verify", json={"token": raw_token, "code": first_code})
    assert stale.status_code == 400

    live_count = await db.scalar(
        select(func.count())
        .select_from(OtpCode)
        .where(
            OtpCode.email == email,
            OtpCode.consumed_at.is_(None),
            OtpCode.expires_at > utc_now(),
        )
    )
    assert live_count == 1


# ══════════════════════════════════════════════════════════════════════════
#  login, lockout, enumeration — FR-109, SPEC §8.3
# ══════════════════════════════════════════════════════════════════════════
async def test_unknown_and_known_emails_are_indistinguishable(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Identical status, code, and message. Timing is equalized by
    `waste_password_time`, which this asserts indirectly by checking the failure
    path is reached at all rather than short-circuiting."""
    known = await make_user(db, role=RoleKey.WORKER)
    known_email = known.email

    wrong_password = await api.post(
        f"{API}/login", json={"email": known_email, "password": "definitely-not-it"}
    )
    no_such_user = await api.post(
        f"{API}/login", json={"email": unique_email("ghost"), "password": "definitely-not-it"}
    )

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json()["code"] == no_such_user.json()["code"]
    assert wrong_password.json()["detail"] == no_such_user.json()["detail"]


async def test_ten_failed_logins_lock_the_account_and_are_audited(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    rate_limit_counters: dict[str, int],
) -> None:
    user = await make_user(db, role=RoleKey.WORKER)
    email = user.email

    for _ in range(10):
        response = await api.post(
            f"{API}/login", json={"email": email, "password": "wrong-password"}
        )
        assert response.status_code == 401

    await db.refresh(user)
    assert user.locked_until is not None and user.locked_until > utc_now()

    # The two controls in SPEC §8.3 are both set to 10, so the request throttle and
    # the account lock trip on the same attempt and the throttle answers first
    # (per IP as well as per email). Clearing it isolates the lock, which is what
    # this test is about. Worth knowing operationally: a locked-out user sees 429
    # for the rest of the window, then 403.
    rate_limit_counters.clear()

    # The correct password is now refused too, with a distinct code so the UI can
    # explain the wait rather than repeating "wrong password".
    locked = await api.post(f"{API}/login", json={"email": email, "password": TEST_PASSWORD})
    assert locked.status_code == 403
    assert locked.json()["code"] == "account_locked"

    actions = await _audit_actions(db)
    assert "auth.account_locked" in actions
    assert actions.count("auth.login_failed") == 10

    # The user is told, so a lockout they did not cause is visible to them.
    payload = await _outbox_payload(db, "account_locked")
    assert payload["to_email"] == email


async def test_a_successful_login_clears_the_failure_counter(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    user = await make_user(db, role=RoleKey.WORKER)
    email = user.email

    for _ in range(3):
        await api.post(f"{API}/login", json={"email": email, "password": "wrong"})
    await db.refresh(user)
    assert user.failed_login_count == 3

    ok = await api.post(f"{API}/login", json={"email": email, "password": TEST_PASSWORD})
    assert ok.status_code == 200

    await db.refresh(user)
    assert user.failed_login_count == 0
    assert user.locked_until is None


async def test_a_deactivated_user_cannot_sign_in(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Same message as a wrong password — whether an account is deactivated is not
    something an unauthenticated caller needs to learn."""
    user = await make_user(db, role=RoleKey.WORKER, status=UserStatus.DEACTIVATED)

    response = await api.post(f"{API}/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect."


# ══════════════════════════════════════════════════════════════════════════
#  refresh rotation and reuse detection — SPEC §8.2
# ══════════════════════════════════════════════════════════════════════════
async def test_refresh_rotates_the_token(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    user = await make_user(db, role=RoleKey.WORKER)
    await api.post(f"{API}/login", json={"email": user.email, "password": TEST_PASSWORD})
    first_cookie = api.cookies[REFRESH_COOKIE]

    assert (await api.post(f"{API}/refresh")).status_code == 200
    assert api.cookies[REFRESH_COOKIE] != first_cookie, "the refresh token was not rotated"

    # The spent token is marked, not deleted — reuse detection needs the record.
    rows = list(await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id)))
    assert len(rows) == 2
    assert sum(1 for row in rows if row.revoked_at is not None) == 1
    assert len({row.family_id for row in rows}) == 1, "a rotation must stay in one family"


async def test_replaying_a_rotated_token_revokes_the_whole_family(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The property that turns token theft into a single-use event."""
    user = await make_user(db, role=RoleKey.WORKER)
    await api.post(f"{API}/login", json={"email": user.email, "password": TEST_PASSWORD})

    stolen = api.cookies[REFRESH_COOKIE]
    assert (await api.post(f"{API}/refresh")).status_code == 200

    # Set on the client rather than per-request: httpx deprecated per-request
    # cookies, and this suite turns DeprecationWarning into an error.
    api.cookies.set(REFRESH_COOKIE, stolen, path="/api/v1/auth")
    replay = await api.post(f"{API}/refresh")
    assert replay.status_code == 401

    rows = list(await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id)))
    assert rows, "no tokens were issued"
    assert all(row.revoked_at is not None for row in rows), (
        "a replay must revoke every token in the family, including the legitimate current one"
    )

    actions = await _audit_actions(db)
    assert "auth.token_reuse_detected" in actions

    payload = await _outbox_payload(db, "account_locked")
    assert payload["to_email"] == user.email


async def test_refresh_without_a_cookie_is_unauthenticated(api: AsyncClient) -> None:
    assert (await api.post(f"{API}/refresh")).status_code == 401


async def test_logout_all_requires_a_bearer_token(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Revoking every device is destructive, so possession of one cookie is not
    enough — it needs proof of an active session."""
    user = await make_user(db, role=RoleKey.WORKER)
    login = await api.post(f"{API}/login", json={"email": user.email, "password": TEST_PASSWORD})

    assert (await api.post(f"{API}/logout-all")).status_code == 401

    authorized = await api.post(
        f"{API}/logout-all",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert authorized.status_code == 200

    rows = list(await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id)))
    assert all(row.revoked_at is not None for row in rows)


# ══════════════════════════════════════════════════════════════════════════
#  password reset — FR-108
# ══════════════════════════════════════════════════════════════════════════
async def test_password_reset_request_is_accepted_for_an_unknown_address(
    api: AsyncClient,
) -> None:
    """202 either way. A different response for a registered address is a user
    enumeration oracle."""
    response = await api.post(
        f"{API}/password-reset/request", json={"email": unique_email("nobody")}
    )
    assert response.status_code == 202


async def test_password_reset_revokes_every_session(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """If the reset happened because an account was compromised, leaving old
    sessions alive would defeat the point."""
    from app.models.auth import PasswordResetToken

    user = await make_user(db, role=RoleKey.WORKER)
    email, user_id = user.email, user.id
    await api.post(f"{API}/login", json={"email": email, "password": TEST_PASSWORD})

    assert (
        await api.post(f"{API}/password-reset/request", json={"email": email})
    ).status_code == 202

    payload = await _outbox_payload(db, "password_reset")
    assert payload["to_email"] == email
    raw_token = payload["context"]["reset_url"].rsplit("/", 1)[-1]

    confirmed = await api.post(
        f"{API}/password-reset/confirm",
        json={"token": raw_token, "password": "a-brand-new-passphrase"},
    )
    assert confirmed.status_code == 200

    rows = list(await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)))
    assert rows and all(row.revoked_at is not None for row in rows)

    # Single use.
    reused = await api.post(
        f"{API}/password-reset/confirm",
        json={"token": raw_token, "password": "yet-another-passphrase"},
    )
    assert reused.status_code == 410

    # And the new password works while the old one does not.
    assert (
        await api.post(f"{API}/login", json={"email": email, "password": TEST_PASSWORD})
    ).status_code == 401
    assert (
        await api.post(f"{API}/login", json={"email": email, "password": "a-brand-new-passphrase"})
    ).status_code == 200

    token_row = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
    )
    assert token_row is not None and token_row.consumed_at is not None


async def test_a_weak_password_is_rejected_with_field_errors(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    ticket = await _reach_registration_ticket(api, db, raw_token, invitation.email)

    response = await api.post(
        f"{API}/register",
        json={
            "registration_ticket": ticket,
            "full_name": "Short Password",
            "password": "short",
        },
    )
    assert response.status_code == 422
    assert any(error["field"] == "password" for error in response.json()["errors"])
