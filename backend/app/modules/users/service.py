"""Profile reads and writes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.modules.auth import authz
from app.modules.auth import service as auth_service
from app.schemas.users import MeResponse, MeUpdate


async def build_me(db: AsyncSession, user: User) -> MeResponse:
    """The caller's own record, with effective permissions resolved live.

    `use_cache=False`: this response is what the SPA rebuilds its permission-gated
    UI from, and it is the endpoint a user hits after being told their access
    changed. Serving a five-minute-old set here would make the one screen whose
    job is to reflect a change the last screen to show it.
    """
    roles = await auth_service.load_role_keys(db, user.id)
    permissions = await authz.effective_permissions(db, user.id, use_cache=False)

    return MeResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        phone=user.phone,
        avatar_url=user.avatar_url,
        locale=user.locale,
        timezone=user.timezone,
        status=user.status,
        roles=roles,
        permissions=sorted(permissions),
        last_login_at=user.last_login_at,
    )


def apply_profile_update(user: User, payload: MeUpdate) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mutate `user` in place and return the audit `(before, after)` diff.

    Only fields that actually changed appear in the diff. An audit row listing
    every field on every save buries the one edit that mattered — and "the phone
    number changed" is exactly the kind of thing someone reads this log to find.
    """
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    changes: dict[str, Any] = {}
    if payload.full_name is not None:
        changes["full_name"] = payload.full_name
    if payload.phone_cleared:
        changes["phone"] = None
    elif payload.phone is not None:
        changes["phone"] = payload.phone
    if payload.locale is not None:
        changes["locale"] = payload.locale
    if payload.timezone is not None:
        changes["timezone"] = payload.timezone

    for field, new_value in changes.items():
        current = getattr(user, field)
        if current == new_value:
            continue
        before[field] = str(current) if current is not None else None
        after[field] = str(new_value) if new_value is not None else None
        setattr(user, field, new_value)

    return before, after
