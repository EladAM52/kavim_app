"""Self-service profile endpoints (SPEC §6.3, §9.3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.modules.audit import service as audit
from app.modules.auth.dependencies import AuthenticatedPrincipal, client_ip, user_agent
from app.modules.users import service
from app.schemas.users import MeResponse, MeUpdate

logger = get_logger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/me",
    response_model=MeResponse,
    summary="The signed-in user's profile and effective permissions",
)
async def read_me(principal: AuthenticatedPrincipal, db: DbSession) -> MeResponse:
    """No permission required — reading your own record is identity, not privilege.

    This is the endpoint the SPA calls to notice that an administrator changed
    its permissions, so it resolves live rather than from cache.
    """
    return await service.build_me(db, principal.user)


@router.patch("/me", response_model=MeResponse, summary="Update your own profile")
async def update_me(
    payload: MeUpdate,
    request: Request,
    principal: AuthenticatedPrincipal,
    db: DbSession,
) -> MeResponse:
    """Name, phone, locale, and timezone. Nothing that grants access.

    No permission-cache invalidation: none of these fields participates in an
    authorization decision. If that ever stops being true, the invalidation call
    belongs here and this comment is the thing that should have been checked.
    """
    before, after = service.apply_profile_update(principal.user, payload)

    if after:
        await audit.write_audit(
            db,
            action=audit.USER_PROFILE_UPDATED,
            entity_type="user",
            entity_id=principal.id,
            actor_id=principal.id,
            before=before,
            after=after,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )

    # Before the response, not after: `get_db` commits in a `yield` teardown that
    # FastAPI runs once the response is already sent, so a client that re-reads
    # immediately can beat its own write (see the note in `auth/router.py`).
    await db.flush()
    await db.commit()

    return await service.build_me(db, principal.user)
