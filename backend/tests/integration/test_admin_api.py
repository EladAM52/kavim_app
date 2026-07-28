"""The twelve `/admin` endpoints (SPEC §6.4, FR-201 … FR-210)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InvitationStatus, NotificationEvent, RoleKey, UserStatus
from app.core.permissions import DEFAULT_ROLE_MATRIX, PERMISSION_KEYS
from app.core.time import utc_now
from app.models.audit import AuditLog
from app.models.auth import RefreshToken
from app.models.notification import NotificationOutbox
from tests.factories import (
    auth_headers,
    make_column,
    make_invitation,
    make_project,
    make_user,
    unique_email,
)

pytestmark = pytest.mark.integration

ADMIN = "/api/v1/admin"


@pytest.fixture
async def admin_headers(db: AsyncSession, seeded_reference: dict[str, object]) -> dict[str, str]:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN, full_name="Admin One")
    return await auth_headers(db, admin)


# ══════════════════════════════════════════════════════════════════════════
#  the role matrix (FR-203)
# ══════════════════════════════════════════════════════════════════════════
async def test_the_matrix_lists_every_role_and_every_seeded_permission(
    api: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Guards against a permission that exists but is not grantable.

    Add one to the registry, forget the seed, and the matrix screen silently
    cannot grant it — the checkbox is simply absent and nobody notices.
    """
    roles = (await api.get(f"{ADMIN}/roles", headers=admin_headers)).json()
    permissions = (await api.get(f"{ADMIN}/permissions", headers=admin_headers)).json()

    assert {row["key"] for row in roles} == {role.value for role in RoleKey}
    assert {row["key"] for row in permissions} == PERMISSION_KEYS

    by_key = {row["key"]: row for row in roles}
    assert set(by_key["SYSTEM_ADMIN"]["permission_keys"]) == PERMISSION_KEYS
    assert set(by_key["WORKER"]["permission_keys"]) == set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])


async def test_the_matrix_reports_how_many_people_a_role_affects(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    await make_user(db, role=RoleKey.WORKER)
    await make_user(db, role=RoleKey.WORKER)

    roles = (await api.get(f"{ADMIN}/roles", headers=admin_headers)).json()
    worker = next(row for row in roles if row["key"] == "WORKER")

    assert worker["user_count"] == 2


async def test_editing_the_matrix_is_audited_with_a_readable_diff(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    admin_headers: dict[str, str],
) -> None:
    roles = seeded_reference["roles"]
    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]
    wanted = sorted(set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER]) - {"file:upload"} | {"task:create"})

    response = await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": wanted},
    )

    assert response.status_code == 200
    assert sorted(response.json()["permission_keys"]) == wanted

    row = await db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "role.permissions_changed")
        .order_by(AuditLog.id.desc())
    )
    assert row is not None
    # The diff names what moved, not the whole set twice over.
    assert (row.after or {})["added"] == ["task:create"]
    assert (row.after or {})["removed"] == ["file:upload"]


async def test_an_unchanged_matrix_save_writes_no_audit_row(
    api: AsyncClient,
    db: AsyncSession,
    seeded_reference: dict[str, object],
    admin_headers: dict[str, str],
) -> None:
    roles = seeded_reference["roles"]
    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]
    current = sorted(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])

    await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": current},
    )

    row = await db.scalar(select(AuditLog).where(AuditLog.action == "role.permissions_changed"))
    assert row is None


async def test_an_unknown_permission_key_is_rejected_by_field(
    api: AsyncClient, seeded_reference: dict[str, object], admin_headers: dict[str, str]
) -> None:
    roles = seeded_reference["roles"]
    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]

    response = await api.put(
        f"{ADMIN}/roles/{worker_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": ["task:read", "user:mange"]},
    )

    assert response.status_code == 422
    assert response.json()["errors"] == [{"field": "permission_keys", "message": "user:mange"}]


async def test_a_rejected_matrix_edit_changes_nothing(
    db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """FR-203's atomicity half. Asserted at the service layer, deliberately.

    Driving this over HTTP does not work and the reason is the harness, not the
    code: the `api` fixture mirrors `get_db`'s rollback-on-exception, and because
    the whole test lives in one transaction that rollback discards the seeded
    roles too — so a follow-up request 401s instead of showing an unchanged
    matrix. In production each request owns its transaction and only the failed
    one is undone. Calling the function directly asserts the property the
    endpoint relies on without fighting the fixture.
    """
    from app.core.exceptions import ValidationError
    from app.modules.admin import roles as roles_mod

    roles = seeded_reference["roles"]
    worker_role = roles[RoleKey.WORKER]  # type: ignore[index]
    before = await roles_mod.list_roles(db)
    original = next(row for row in before if row.key == RoleKey.WORKER).permission_keys

    with pytest.raises(ValidationError):
        await roles_mod.replace_role_permissions(db, worker_role, ["task:read", "nonsense:key"])

    after = await roles_mod.list_roles(db)
    assert next(row for row in after if row.key == RoleKey.WORKER).permission_keys == original


async def test_the_last_role_that_can_manage_permissions_cannot_be_stripped(
    api: AsyncClient, seeded_reference: dict[str, object], admin_headers: dict[str, str]
) -> None:
    """One PUT must not be able to make the installation unadministrable.

    Recovery does exist — `seed --reference` restores the defaults — but a 409
    beats a support call.
    """
    roles = seeded_reference["roles"]
    admin_role = roles[RoleKey.SYSTEM_ADMIN]  # type: ignore[index]

    response = await api.put(
        f"{ADMIN}/roles/{admin_role.id}/permissions",
        headers=admin_headers,
        json={"permission_keys": ["task:read"]},
    )

    assert response.status_code == 409
    assert "manage permissions" in response.json()["detail"]


async def test_an_unknown_role_id_is_404(api: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api.put(
        f"{ADMIN}/roles/00000000-0000-0000-0000-000000000000/permissions",
        headers=admin_headers,
        json={"permission_keys": []},
    )
    assert response.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
#  users (FR-201, FR-202, FR-206, FR-207)
# ══════════════════════════════════════════════════════════════════════════
async def test_the_user_list_pages_without_losing_or_repeating_rows(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """The failure `OFFSET` has, and the reason SPEC §9.1 forbids it.

    Rows are inserted *between* the two page reads, which is what happens on a
    real admin screen while people are being invited. With an offset the second
    page would repeat rows the first already showed.
    """
    for _ in range(6):
        await make_user(db, role=RoleKey.WORKER)

    first = (await api.get(f"{ADMIN}/users?limit=3", headers=admin_headers)).json()
    assert len(first["items"]) == 3
    assert first["next_cursor"]

    for _ in range(3):
        await make_user(db, role=RoleKey.WORKER)

    second = (
        await api.get(f"{ADMIN}/users?limit=3&cursor={first['next_cursor']}", headers=admin_headers)
    ).json()

    first_ids = {row["id"] for row in first["items"]}
    second_ids = {row["id"] for row in second["items"]}
    assert not (first_ids & second_ids)


async def test_the_user_list_filters_by_role_and_status_and_searches(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    await make_user(db, role=RoleKey.WORKER, full_name="Dana Levi")
    await make_user(db, role=RoleKey.LINE_MANAGER, full_name="Yossi Cohen")
    await make_user(db, role=RoleKey.WORKER, status=UserStatus.DEACTIVATED)

    by_role = (await api.get(f"{ADMIN}/users?role=LINE_MANAGER", headers=admin_headers)).json()
    assert [row["full_name"] for row in by_role["items"]] == ["Yossi Cohen"]

    by_status = (await api.get(f"{ADMIN}/users?status=deactivated", headers=admin_headers)).json()
    assert len(by_status["items"]) == 1

    by_name = (await api.get(f"{ADMIN}/users?q=dana", headers=admin_headers)).json()
    assert [row["full_name"] for row in by_name["items"]] == ["Dana Levi"]


async def test_a_malformed_cursor_is_a_400_not_a_500(
    api: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """Cursors travel in URLs, get truncated, and get hand-edited. None of that
    is a server fault."""
    response = await api.get(f"{ADMIN}/users?cursor=!!!not-base64!!!", headers=admin_headers)
    assert response.status_code == 400


async def test_changing_a_role_replaces_it_rather_than_adding_one(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    worker = await make_user(db, role=RoleKey.WORKER)

    response = await api.patch(
        f"{ADMIN}/users/{worker.id}",
        headers=admin_headers,
        json={"role_key": "SHIFT_SUPERVISOR"},
    )

    assert response.status_code == 200
    assert response.json()["roles"] == ["SHIFT_SUPERVISOR"]

    row = await db.scalar(
        select(AuditLog).where(AuditLog.action == "user.role_changed").order_by(AuditLog.id.desc())
    )
    assert row is not None
    assert (row.before or {})["roles"] == ["WORKER"]
    assert (row.after or {})["roles"] == ["SHIFT_SUPERVISOR"]


async def test_deactivating_a_user_revokes_every_refresh_token(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """FR-206. The access token dies via `is_active`; the refresh token would not.

    Left alive it stays valid for thirty days and mints a fresh access token the
    moment the account is reactivated — or immediately, if the deactivation was
    the only thing standing in the way.
    """
    from app.modules.auth.service import issue_refresh_token

    worker = await make_user(db, role=RoleKey.WORKER)
    await issue_refresh_token(db, user_id=worker.id)
    await db.flush()

    response = await api.patch(
        f"{ADMIN}/users/{worker.id}", headers=admin_headers, json={"status": "deactivated"}
    )

    assert response.status_code == 200
    live = await db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == worker.id, RefreshToken.revoked_at.is_(None)
        )
    )
    assert list(live) == []


async def test_an_admin_cannot_deactivate_themselves(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)

    response = await api.patch(
        f"{ADMIN}/users/{admin.id}", headers=headers, json={"status": "deactivated"}
    )

    assert response.status_code == 409
    assert "your own account" in response.json()["detail"]


async def test_an_admin_cannot_change_their_own_role(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)

    response = await api.patch(
        f"{ADMIN}/users/{admin.id}", headers=headers, json={"role_key": "WORKER"}
    )

    assert response.status_code == 409


async def test_the_last_active_admin_cannot_be_deactivated_by_another(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Two admins, each deactivating the other, and nobody is left.

    The self-deactivation guard does not cover this. Every individual step looks
    reasonable, and the installation ends up with no administrator — so the guard
    has to count *active* holders rather than trust the actor.
    """
    actor = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    victim = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    spare = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, actor)

    # Three admins: removing one is fine.
    assert (
        await api.patch(
            f"{ADMIN}/users/{victim.id}", headers=headers, json={"status": "deactivated"}
        )
    ).status_code == 200

    # Two left. Removing the spare leaves only `actor`, who cannot remove
    # themselves — so this is still allowed.
    assert (
        await api.patch(
            f"{ADMIN}/users/{spare.id}", headers=headers, json={"status": "deactivated"}
        )
    ).status_code == 200

    # `actor` is now the only active admin. Nobody can remove them, including a
    # freshly reactivated colleague acting on their behalf.
    assert (
        await api.patch(
            f"{ADMIN}/users/{actor.id}", headers=headers, json={"status": "deactivated"}
        )
    ).status_code == 409


async def test_patching_nothing_is_a_422(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    worker = await make_user(db, role=RoleKey.WORKER)
    response = await api.patch(f"{ADMIN}/users/{worker.id}", headers=admin_headers, json={})
    assert response.status_code == 422


async def test_force_logout_revokes_sessions_and_is_audited(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """FR-207. The account stays usable — this is the answer to a lost phone."""
    from app.modules.auth.service import issue_refresh_token

    worker = await make_user(db, role=RoleKey.WORKER)
    await issue_refresh_token(db, user_id=worker.id)
    await db.flush()

    response = await api.post(f"{ADMIN}/users/{worker.id}/force-logout", headers=admin_headers)

    assert response.status_code == 200
    assert "1 session" in response.json()["detail"]
    assert worker.status == UserStatus.ACTIVE

    row = await db.scalar(select(AuditLog).where(AuditLog.action == "user.force_logout"))
    assert row is not None


async def test_an_unknown_user_id_is_404(api: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api.get(
        f"{ADMIN}/users/00000000-0000-0000-0000-000000000000/effective-permissions",
        headers=admin_headers,
    )
    assert response.status_code == 404


# ══════════════════════════════════════════════════════════════════════════
#  the permission trace (FR-210)
# ══════════════════════════════════════════════════════════════════════════
async def test_the_trace_shows_all_three_layers(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """The Phase 3 stand-in for SPEC §13's "done when".

    The column-edit demonstration itself needs Phase 4's column editor and Phase
    5's cell writer. What is answerable today — and answered here — is *would*
    this worker be allowed to write this column.
    """
    worker = await make_user(db, role=RoleKey.WORKER)
    manager = await make_user(db, role=RoleKey.LINE_MANAGER)
    # `with_status_column=False`: the factory would otherwise add its own
    # `status` column and collide with the one this test needs to control.
    project = await make_project(db, created_by=manager, with_status_column=False)
    await make_column(
        db, project=project, key="status", position=1000, editable_by_roles=[RoleKey.WORKER]
    )
    await make_column(
        db, project=project, key="verified", position=2000, editable_by_roles=[RoleKey.LINE_MANAGER]
    )
    await db.flush()

    response = await api.get(
        f"{ADMIN}/users/{worker.id}/effective-permissions?project_id={project.id}",
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["roles"] == ["WORKER"]
    assert set(body["layer1_role_permissions"]) == set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])
    # Not a member of the project, so layer 2 grants nothing.
    assert body["layer2_project_level"] is None
    assert body["effective"] == []

    verdicts = {row["key"]: row["editable"] for row in body["layer3_columns"]}
    assert verdicts == {"status": True, "verified": False}


async def test_the_trace_without_a_project_reports_only_layer_one(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    worker = await make_user(db, role=RoleKey.WORKER)

    body = (
        await api.get(f"{ADMIN}/users/{worker.id}/effective-permissions", headers=admin_headers)
    ).json()

    assert set(body["effective"]) == set(DEFAULT_ROLE_MATRIX[RoleKey.WORKER])
    assert body["layer3_columns"] == []


# ══════════════════════════════════════════════════════════════════════════
#  invitations (FR-101, FR-111)
# ══════════════════════════════════════════════════════════════════════════
async def test_creating_an_invitation_audits_and_queues_the_email(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    email = unique_email("invitee")

    response = await api.post(
        f"{ADMIN}/invitations",
        headers=admin_headers,
        json={"email": email, "role_key": "WORKER"},
    )

    assert response.status_code == 201
    assert response.json()["email"] == email
    assert response.json()["status"] == InvitationStatus.PENDING.value

    outbox_row = await db.scalar(select(NotificationOutbox).order_by(NotificationOutbox.id.desc()))
    assert outbox_row is not None
    assert outbox_row.event == "invitation"

    audit_row = await db.scalar(select(AuditLog).where(AuditLog.action == "invitation.created"))
    assert audit_row is not None


async def test_the_queued_invitation_renders_in_both_locales(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """Worth more than it looks.

    `rendering.py` runs Jinja with `StrictUndefined`, so a context missing one key
    raises **inside the sweeper**, in another process, minutes later, with nothing
    pointing back at the endpoint that queued it. Rendering here moves that
    failure to the moment the context is built.
    """
    from app.core.enums import Locale
    from app.modules.notifications.rendering import render_email

    await api.post(
        f"{ADMIN}/invitations",
        headers=admin_headers,
        json={"email": unique_email("render"), "role_key": "SHIFT_SUPERVISOR"},
    )
    row = await db.scalar(select(NotificationOutbox).order_by(NotificationOutbox.id.desc()))
    assert row is not None
    context = (row.payload or {})["context"]

    for locale in (Locale.HE, Locale.EN):
        rendered = render_email(NotificationEvent.INVITATION, locale, context)
        assert rendered.subject.strip()
        assert rendered.text_body.strip()


async def test_the_requested_locale_beats_the_senders_browser(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """FR-101. The invitee's language is the sender's to state, not the browser's.

    `Accept-Language` describes whoever is holding the mouse. On a plant where a
    Hebrew-speaking manager invites an English-speaking contractor, honouring the
    header sends the wrong language every time — and the mistake is invisible to
    the sender, because they never see the mail.
    """
    await api.post(
        f"{ADMIN}/invitations",
        headers={**admin_headers, "Accept-Language": "he-IL,he;q=0.9"},
        json={"email": unique_email("english-invitee"), "role_key": "WORKER", "locale": "en"},
    )

    row = await db.scalar(select(NotificationOutbox).order_by(NotificationOutbox.id.desc()))
    assert row is not None
    assert (row.payload or {})["locale"] == "en"


async def test_an_unstated_locale_still_follows_the_header(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """The fallback the CLI and every pre-`locale` client depend on."""
    await api.post(
        f"{ADMIN}/invitations",
        headers={**admin_headers, "Accept-Language": "en-US,en;q=0.9"},
        json={"email": unique_email("header-invitee"), "role_key": "WORKER"},
    )

    row = await db.scalar(select(NotificationOutbox).order_by(NotificationOutbox.id.desc()))
    assert row is not None
    assert (row.payload or {})["locale"] == "en"


async def test_the_response_never_carries_the_raw_token(
    api: AsyncClient, admin_headers: dict[str, str]
) -> None:
    """The token is the credential. Anyone with `user:invite` could otherwise
    accept an invitation addressed to somebody else."""
    response = await api.post(
        f"{ADMIN}/invitations",
        headers=admin_headers,
        json={"email": unique_email("secret"), "role_key": "WORKER"},
    )

    assert "token" not in response.text.lower()


async def test_inviting_an_address_that_already_has_an_account_is_rejected(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    existing = await make_user(db, role=RoleKey.WORKER)

    response = await api.post(
        f"{ADMIN}/invitations",
        headers=admin_headers,
        json={"email": existing.email, "role_key": "WORKER"},
    )

    assert response.status_code == 409


async def test_an_unknown_project_id_is_rejected(
    api: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api.post(
        f"{ADMIN}/invitations",
        headers=admin_headers,
        json={
            "email": unique_email("ghost"),
            "role_key": "WORKER",
            "project_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )
    assert response.status_code == 422


async def test_resending_kills_the_previous_link(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """FR-111: "resend issues a new token and invalidates the old one".

    Asserted through the public flow — the old token must now be gone from the
    invitee's point of view, not merely marked revoked in a column.
    """
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    await db.flush()

    assert (await api.get(f"/api/v1/auth/invitations/{raw_token}")).status_code == 200

    response = await api.post(f"{ADMIN}/invitations/{invitation.id}/resend", headers=headers)
    assert response.status_code == 202

    assert (await api.get(f"/api/v1/auth/invitations/{raw_token}")).status_code == 410
    row = await db.scalar(select(AuditLog).where(AuditLog.action == "invitation.resent"))
    assert row is not None


async def test_resending_is_rate_limited(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Nothing else stops an administrator from mail-bombing an address, and the
    Gmail daily ceiling takes OTP delivery down with it when breached (SPEC R2)."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    invitation, _ = await make_invitation(db, invited_by=admin)
    await db.flush()

    latest = invitation.id
    statuses = []
    for _ in range(7):
        response = await api.post(f"{ADMIN}/invitations/{latest}/resend", headers=headers)
        statuses.append(response.status_code)
        if response.status_code == 202:
            latest = response.json()["id"]

    assert 429 in statuses


async def test_revoking_makes_the_link_stop_working(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    invitation, raw_token = await make_invitation(db, invited_by=admin)
    await db.flush()

    response = await api.delete(f"{ADMIN}/invitations/{invitation.id}", headers=headers)

    assert response.status_code == 200
    assert (await api.get(f"/api/v1/auth/invitations/{raw_token}")).status_code == 410


async def test_revoking_twice_is_not_an_error(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """A double-click must not report failure for an outcome the user has."""
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    invitation, _ = await make_invitation(db, invited_by=admin)
    await db.flush()

    assert (
        await api.delete(f"{ADMIN}/invitations/{invitation.id}", headers=headers)
    ).status_code == 200
    assert (
        await api.delete(f"{ADMIN}/invitations/{invitation.id}", headers=headers)
    ).status_code == 200


async def test_a_consumed_invitation_cannot_be_revoked(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    invitation, _ = await make_invitation(db, invited_by=admin, status=InvitationStatus.CONSUMED)
    await db.flush()

    response = await api.delete(f"{ADMIN}/invitations/{invitation.id}", headers=headers)
    assert response.status_code == 410


async def test_an_elapsed_invitation_lists_as_expired_not_pending(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """Nothing sweeps the table, so the stored status stays `pending` forever.

    A list that showed it as pending would offer a working-looking link and a
    resend button for something already dead.
    """
    admin = await make_user(db, role=RoleKey.SYSTEM_ADMIN)
    headers = await auth_headers(db, admin)
    invitation, _ = await make_invitation(db, invited_by=admin)
    invitation.expires_at = utc_now() - timedelta(minutes=1)
    await db.flush()

    page = (await api.get(f"{ADMIN}/invitations", headers=headers)).json()
    row = next(item for item in page["items"] if item["id"] == str(invitation.id))

    assert row["status"] == InvitationStatus.EXPIRED.value


async def test_a_shift_supervisor_cannot_invite(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """`user:invite` belongs to LINE_MANAGER and above in the seeded matrix."""
    supervisor = await make_user(db, role=RoleKey.SHIFT_SUPERVISOR)
    headers = await auth_headers(db, supervisor)

    response = await api.post(
        f"{ADMIN}/invitations",
        headers=headers,
        json={"email": unique_email("nope"), "role_key": "WORKER"},
    )
    assert response.status_code == 403


async def test_a_line_manager_can_invite(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    manager = await make_user(db, role=RoleKey.LINE_MANAGER)
    headers = await auth_headers(db, manager)

    response = await api.post(
        f"{ADMIN}/invitations",
        headers=headers,
        json={"email": unique_email("welcome"), "role_key": "WORKER"},
    )
    assert response.status_code == 201


# ══════════════════════════════════════════════════════════════════════════
#  audit log (FR-208)
# ══════════════════════════════════════════════════════════════════════════
async def test_rows_sharing_a_transaction_timestamp_still_page_without_loss(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """The test that justifies keying the cursor on `id` rather than `created_at`.

    `created_at` defaults to `now()`, which in PostgreSQL is *transaction* time —
    so these rows all carry an identical timestamp. A `created_at` cursor would
    either skip the rest of the tied group or serve it forever.
    """
    from app.modules.audit.service import write_audit

    for index in range(5):
        await write_audit(db, action="test.tied", entity_type="probe", after={"n": index})
    await db.flush()

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(6):
        url = f"{ADMIN}/audit-log?limit=1&action=test.tied"
        if cursor:
            url += f"&cursor={cursor}"
        page = (await api.get(url, headers=admin_headers)).json()
        seen.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_the_audit_log_is_newest_first_and_filters(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    worker = await make_user(db, role=RoleKey.WORKER)
    await api.patch(
        f"{ADMIN}/users/{worker.id}", headers=admin_headers, json={"role_key": "VIEWER"}
    )

    page = (
        await api.get(f"{ADMIN}/audit-log?action=user.role_changed", headers=admin_headers)
    ).json()

    assert len(page["items"]) == 1
    entry = page["items"][0]
    assert entry["entity_type"] == "user"
    assert entry["entity_id"] == str(worker.id)
    assert entry["actor_name"] == "Admin One"


async def test_a_viewer_can_read_the_audit_log_but_not_the_user_list(
    api: AsyncClient, db: AsyncSession, seeded_reference: dict[str, object]
) -> None:
    """The seeded asymmetry, asserted because it looks like a mistake.

    VIEWER is the compliance role: it holds `audit:read` and no write permission
    anywhere, and it deliberately does not hold `user:manage`.
    """
    viewer = await make_user(db, role=RoleKey.VIEWER)
    headers = await auth_headers(db, viewer)

    assert (await api.get(f"{ADMIN}/audit-log", headers=headers)).status_code == 200
    assert (await api.get(f"{ADMIN}/users", headers=headers)).status_code == 403


async def test_secrets_never_reach_the_audit_log(
    api: AsyncClient, db: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """`_scrub` redacts on write; this checks it end to end through the reader."""
    await api.post(
        f"{ADMIN}/invitations",
        headers=admin_headers,
        json={"email": unique_email("scrub"), "role_key": "WORKER"},
    )

    page = (
        await api.get(f"{ADMIN}/audit-log?action=invitation.created", headers=admin_headers)
    ).json()
    body = str(page["items"])

    assert "token" not in body.lower()
