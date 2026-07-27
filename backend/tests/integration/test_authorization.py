"""The authorization mechanism itself (SPEC §8.4, CLAUDE.md rule 2).

Separate from `test_admin_api.py` on purpose. That file asks whether each endpoint
behaves correctly; this one asks whether `require_permission`, the resolver, and
the cache behave correctly — so a failure here points at one layer rather than at
twelve endpoints.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RoleKey
from app.models.audit import AuditLog
from app.models.role import UserRole
from app.modules.auth import authz
from tests.factories import auth_headers, make_user

pytestmark = pytest.mark.integration

ADMIN = "/api/v1/admin"

# Every admin route, with a body where one is required. Parametrized so a route
# added without thinking about a worker hitting it fails here too.
ADMIN_ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", f"{ADMIN}/permissions", None),
    ("GET", f"{ADMIN}/roles", None),
    ("GET", f"{ADMIN}/users", None),
    ("GET", f"{ADMIN}/audit-log", None),
    ("GET", f"{ADMIN}/invitations", None),
    ("POST", f"{ADMIN}/invitations", {"email": "x@example.com", "role_key": "WORKER"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_a_worker_is_forbidden_from_every_admin_route(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    worker = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, worker)

    response = await api.request(method, path, headers=headers, json=body)

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "permission_denied"


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
async def test_an_admin_reaches_every_admin_route(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """The other half of the parametrized denial test.

    Without it, a route that is broken for *everyone* would look like correct
    authorization — the 403 test would pass on a 403 that had nothing to do with
    permissions.
    """
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)

    response = await api.request(method, path, headers=headers, json=body)

    assert response.status_code in {200, 201}, response.text


async def test_an_unauthenticated_admin_request_is_401_not_403(api: AsyncClient) -> None:
    """Ordering: `get_current_user` must answer before the permission check.

    A 403 here would tell an anonymous caller that the route exists and that their
    (nonexistent) identity lacks a permission — and would leave the client with no
    signal that signing in is the fix.
    """
    response = await api.get(f"{ADMIN}/users")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_permissions_come_from_the_database_not_the_token(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The JWT's `roles` claim is decorative, and this proves it.

    `create_access_token` embeds roles for debugging and for the SPA's initial
    render. If authorization ever read them, revoking a role would not take effect
    until the token expired — and a forged claim would be an escalation.
    """
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)  # minted while still an admin
    assert (await api.get(f"{ADMIN}/users", headers=headers)).status_code == 200

    await db.execute(UserRole.__table__.delete().where(UserRole.user_id == admin.id))
    await db.flush()
    await authz.invalidate_user(admin.id)

    # Same token, still valid, still claims SYSTEM_ADMIN.
    response = await api.get(f"{ADMIN}/users", headers=headers)
    assert response.status_code == 403


async def test_a_denied_mutation_is_audited_and_the_row_survives_the_403(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """`get_db` rolls back on exception, so the audit row is committed first.

    Without that commit the record of the denial would be erased by the very
    error that recorded it — the defect found three times in Phase 2's login,
    OTP, and refresh paths.
    """
    worker = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, worker)

    response = await api.post(
        f"{ADMIN}/invitations",
        headers=headers,
        json={"email": "nope@example.com", "role_key": "WORKER"},
    )
    assert response.status_code == 403

    row = await db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "auth.permission_denied")
        .order_by(AuditLog.id.desc())
    )
    assert row is not None
    assert row.actor_id == worker.id
    assert (row.after or {})["missing"] == ["user:invite"]
    assert (row.after or {})["method"] == "POST"


async def test_a_denied_read_is_not_audited(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Deliberate, not an oversight.

    A UI that hides a button still polls the endpoint behind it, so auditing
    denied GETs fills the log with noise produced by the client working exactly as
    designed — burying the denied writes that are worth seeing.
    """
    worker = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, worker)

    before = await db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "auth.permission_denied")
    )
    assert (await api.get(f"{ADMIN}/users", headers=headers)).status_code == 403
    after = await db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "auth.permission_denied")
    )

    assert after == before


# ══════════════════════════════════════════════════════════════════════════
#  the cache
# ══════════════════════════════════════════════════════════════════════════
async def test_a_second_request_is_served_from_the_cache(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    permission_cache: dict[str, object],
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)

    assert authz.cache_key(admin.id, None) not in permission_cache
    await api.get(f"{ADMIN}/users", headers=headers)
    assert authz.cache_key(admin.id, None) in permission_cache


async def test_invalidating_one_user_leaves_another_alone(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    permission_cache: dict[str, object],
) -> None:
    """The key includes the user id so a role change is not a global flush.

    A prefix that matched too broadly would work — everything would re-resolve —
    and would quietly turn every role change into a cache stampede.
    """
    first = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    second = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    await api.get(f"{ADMIN}/users", headers=await auth_headers(db, first))
    await api.get(f"{ADMIN}/users", headers=await auth_headers(db, second))

    await authz.invalidate_user(first.id)

    assert authz.cache_key(first.id, None) not in permission_cache
    assert authz.cache_key(second.id, None) in permission_cache


async def test_a_matrix_edit_flushes_the_whole_cache(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    permission_cache: dict[str, object],
) -> None:
    roles = seeded_reference["roles"]
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    await api.get(f"{ADMIN}/users", headers=headers)
    assert permission_cache

    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]
    response = await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=headers,
        json={"permission_keys": ["task:read"]},
    )

    assert response.status_code == 200
    assert permission_cache == {}


async def test_a_matrix_edit_takes_effect_on_the_very_next_request(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """FR-202, and the reason invalidation exists rather than just a TTL.

    No sleep, no re-login, no new token: the worker's *next* call sees the change.
    A revoked permission that survives five minutes is a revoked permission that
    did not work.
    """
    roles = seeded_reference["roles"]
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    worker = await make_user(db, role=RoleKey.WORKER)
    admin_headers = await auth_headers(db, admin)
    worker_headers = await auth_headers(db, worker)

    # Populate the worker's cache, and confirm they cannot list users.
    assert (await api.get("/api/v1/users/me", headers=worker_headers)).status_code == 200
    assert (await api.get(f"{ADMIN}/users", headers=worker_headers)).status_code == 403

    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]
    grant = await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": ["task:read", "user:manage"]},
    )
    assert grant.status_code == 200

    # Same token. Immediately.
    assert (await api.get(f"{ADMIN}/users", headers=worker_headers)).status_code == 200


async def test_revoking_takes_effect_just_as_fast(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The direction that actually matters for security."""
    roles = seeded_reference["roles"]
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    worker = await make_user(db, role=RoleKey.WORKER)
    admin_headers = await auth_headers(db, admin)
    worker_headers = await auth_headers(db, worker)
    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]

    await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": ["task:read", "user:manage"]},
    )
    assert (await api.get(f"{ADMIN}/users", headers=worker_headers)).status_code == 200

    await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": ["task:read"]},
    )
    assert (await api.get(f"{ADMIN}/users", headers=worker_headers)).status_code == 403


async def test_authorization_still_works_when_redis_is_down(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    redis_down: None,
) -> None:
    """Fail soft, not fail open — and not fail closed either.

    A cache miss and a cache outage take the same branch: resolve from
    PostgreSQL. So an admin still gets 200 and a worker still gets 403, with
    Redis unreachable. Failing closed would turn a Redis restart into every user
    seeing 403 on every route.
    """
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    worker = await make_user(db, role=RoleKey.WORKER)

    assert (
        await api.get(f"{ADMIN}/users", headers=await auth_headers(db, admin))
    ).status_code == 200
    assert (
        await api.get(f"{ADMIN}/users", headers=await auth_headers(db, worker))
    ).status_code == 403


async def test_a_corrupt_cache_entry_does_not_grant_access(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    permission_cache: dict[str, object],
) -> None:
    """A non-list value must be ignored, not coerced.

    Redis holds strings. If something else ever writes to this key — a different
    service, a botched migration, a key collision — the resolver must treat it as
    a miss rather than as an answer.
    """
    worker = await make_user(db, role=RoleKey.WORKER)
    headers = await auth_headers(db, worker)
    permission_cache[authz.cache_key(worker.id, None)] = {"user:manage": True}

    assert (await api.get(f"{ADMIN}/users", headers=headers)).status_code == 403
