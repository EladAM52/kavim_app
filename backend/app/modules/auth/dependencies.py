"""Request-scoped authentication dependencies.

`get_current_user` decodes the bearer token and re-loads the user from the
database on every request. That second step is not redundant: a JWT is valid until
it expires, so a user deactivated 30 seconds ago would still authenticate from the
token alone. The 15-minute access-token lifetime is what bounds the window; the
database check is what closes it for deactivation and deletion.

`require_permission` lands in Phase 3 with the admin panel — Phase 2 has no
endpoint that needs it, and writing it before there is something to authorize
would mean guessing at the caller shape.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthenticationError
from app.core.security import TokenError, decode_access_token
from app.models.user import User

# auto_error=False so a missing header raises our own problem+json rather than
# FastAPI's plain JSON, keeping one error shape across the API (SPEC §9.1).
_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def client_ip(request: Request) -> str | None:
    """Best-effort client address for audit rows and rate-limit keys.

    `X-Forwarded-For` is honoured because production sits behind a reverse proxy,
    and only the first hop is taken — the rest of that header is attacker-supplied.
    Never used for an authorization decision, only for attribution and throttling.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    agent = request.headers.get("User-Agent")
    return agent[:500] if agent else None


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Sign in to continue.")

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        # One message for expired, malformed, and wrongly-signed alike: telling an
        # attacker which of the three they achieved is free information.
        raise AuthenticationError("Your session expired. Please sign in again.") from exc

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Your session expired. Please sign in again.") from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Your session is no longer valid. Please sign in again.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
