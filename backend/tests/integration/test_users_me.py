"""`/users/me` — the smallest route that exercises the whole authorization chain.

Worth having early and separately from the admin tests: if `require_authenticated`,
`Principal`, or the resolver is broken, this fails on one trivial endpoint instead
of failing on twelve admin endpoints at once and leaving you to work out which
layer is at fault.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Locale, RoleKey
from app.core.permissions import DEFAULT_ROLE_MATRIX
from tests.factories import TEST_PASSWORD, auth_headers, make_user

pytestmark = pytest.mark.integration

API = "/api/v1/users"


async def test_me_requires_authentication(api: AsyncClient) -> None:
    response = await api.get(f"{API}/me")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "unauthenticated"


async def test_me_returns_the_profile_and_effective_permissions(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    user = await make_user(db, role=RoleKey.WORKER, full_name="עובד קו")
    headers = await auth_headers(db, user)

    response = await api.get(f"{API}/me", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "עובד קו"
    assert body["roles"] == [RoleKey.WORKER.value]
    assert set(body["permissions"]) == set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])
    assert "password_hash" not in body


async def test_me_matches_what_login_reported(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Two code paths build the permission list; they must not diverge.

    `build_identity` serves login and refresh, `build_me` serves this endpoint. A
    difference between them would show as a UI that gains or loses buttons on
    reload, which reads as a rendering bug and is not one.
    """
    user = await make_user(db, role=RoleKey.SHIFT_SUPERVISOR)
    email = user.email

    login = await api.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert login.status_code == 200
    from_login = login.json()["user"]

    me = await api.get(
        f"{API}/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert me.status_code == 200
    from_me = me.json()

    assert from_me["permissions"] == from_login["permissions"]
    assert from_me["roles"] == from_login["roles"]
    assert from_me["id"] == from_login["id"]


async def test_patch_me_updates_the_profile_and_audits_only_what_changed(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    from sqlalchemy import select

    from app.models.audit import AuditLog

    user = await make_user(db, role=RoleKey.WORKER, full_name="Old Name")
    headers = await auth_headers(db, user)

    response = await api.patch(
        f"{API}/me",
        headers=headers,
        json={"full_name": "New Name", "locale": "en", "timezone": "Europe/Berlin"},
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "New Name"
    assert response.json()["locale"] == "en"
    assert response.json()["timezone"] == "Europe/Berlin"

    row = await db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "user.profile_updated")
        .order_by(AuditLog.id.desc())
    )
    assert row is not None
    # The phone was never sent, so it must not appear in the diff.
    assert set(row.after or {}) == {"full_name", "locale", "timezone"}
    assert (row.before or {})["full_name"] == "Old Name"


async def test_patch_me_writes_no_audit_row_when_nothing_changed(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Re-saving an unchanged form is the most common thing a user does.

    An audit row for it is noise in the log somebody reads to find a real change.
    """
    from sqlalchemy import func, select

    from app.models.audit import AuditLog

    user = await make_user(db, role=RoleKey.WORKER, full_name="Same Name")
    headers = await auth_headers(db, user)

    before_count = await db.scalar(select(func.count()).select_from(AuditLog))
    response = await api.patch(f"{API}/me", headers=headers, json={"full_name": "Same Name"})
    after_count = await db.scalar(select(func.count()).select_from(AuditLog))

    assert response.status_code == 200
    assert after_count == before_count


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"email": "someone-else@example.com"}, id="email"),
        pytest.param({"status": "active"}, id="status"),
        pytest.param({"role_key": "SYSTEM_ADMIN"}, id="role"),
        pytest.param({"permissions": ["user:manage"]}, id="permissions"),
    ],
)
async def test_patch_me_refuses_to_change_anything_privilege_bearing(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    payload: dict[str, object],
) -> None:
    """`extra="forbid"` is the control, and this is what it is for.

    Silently ignoring an unknown field would tell a client its self-promotion
    succeeded. A 422 tells it the truth.
    """
    user = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, user)

    response = await api.patch(f"{API}/me", headers=headers, json=payload)

    assert response.status_code == 422


async def test_patch_me_rejects_an_unknown_timezone(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    user = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, user)

    response = await api.patch(f"{API}/me", headers=headers, json={"timezone": "Mars/Olympus_Mons"})

    assert response.status_code == 422


async def test_patch_me_normalizes_an_israeli_phone_to_e164(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    user = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, user)

    response = await api.patch(f"{API}/me", headers=headers, json={"phone": "050-123-4567"})

    assert response.status_code == 200
    assert response.json()["phone"] == "+972501234567"


async def test_patch_me_can_clear_the_phone(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Omitting a field means "leave alone", so removing one needs its own flag."""
    user = await make_user(db, role=RoleKey.WORKER, phone="+972501112222")
    headers = await auth_headers(db, user)

    response = await api.patch(f"{API}/me", headers=headers, json={"phone_cleared": True})

    assert response.status_code == 200
    assert response.json()["phone"] is None


async def test_a_deactivated_user_cannot_read_their_own_record(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """A live access token must not outlive deactivation.

    `get_current_user` re-reads `is_active` from the database on every request
    precisely so the 15-minute token lifetime is not also the deactivation delay.
    """
    from app.core.enums import UserStatus

    user = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, user)
    assert (await api.get(f"{API}/me", headers=headers)).status_code == 200

    user.status = UserStatus.DEACTIVATED
    await db.flush()

    response = await api.get(f"{API}/me", headers=headers)
    assert response.status_code == 401


async def test_me_resolves_live_rather_than_from_cache(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    permission_cache: dict[str, object],
) -> None:
    """This endpoint is where the SPA looks after being told its access changed.

    Serving a cached set here would make the one screen whose job is to reflect a
    change the last screen to show it. Asserted by poisoning the cache and
    checking the response ignores it.
    """
    user = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, user)

    from app.modules.auth import authz

    permission_cache[authz.cache_key(user.id, None)] = ["task:read"]

    response = await api.get(f"{API}/me", headers=headers)

    assert response.status_code == 200
    assert set(response.json()["permissions"]) == set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])


async def test_the_locale_round_trips(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    user = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, user)

    await api.patch(f"{API}/me", headers=headers, json={"locale": "en"})
    response = await api.get(f"{API}/me", headers=headers)

    assert response.json()["locale"] == Locale.EN.value
