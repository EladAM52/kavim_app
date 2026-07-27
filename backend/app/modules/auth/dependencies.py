"""Request-scoped authentication dependencies.

`get_current_user` decodes the bearer token and re-loads the user from the
database on every request. That second step is not redundant: a JWT is valid until
it expires, so a user deactivated 30 seconds ago would still authenticate from the
token alone. The 15-minute access-token lifetime is what bounds the window; the
database check is what closes it for deactivation and deletion.

`require_permission` is the authorization half (SPEC §8.4, FR-209). It is a small
class rather than a closure on purpose — see `PermissionRequirement`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.permissions import PERMISSION_KEYS
from app.core.security import TokenError, decode_access_token
from app.models.user import User
from app.modules.audit import service as audit
from app.modules.auth import authz

logger = get_logger(__name__)

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


# ══════════════════════════════════════════════════════════════════════════
#  authorization
# ══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller together with what they may do this request.

    Handlers take this instead of `CurrentUser` when they are authorized, so the
    permission check and the actor id for the audit row come from one object and
    cannot disagree.

    Roles are deliberately absent. Nothing in Phase 3 needs them for a decision —
    the trace endpoint loads them itself — and putting them here would add a
    second query to every authorized request to serve one endpoint.
    """

    user: User
    permissions: frozenset[str]

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    def has(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    """A route's authorization declaration — an object, not a closure.

    The object form is what makes `tests/security/test_all_routes_declare_permission.py`
    possible. That test must read the required permission back off the route table
    *without executing the route*, and FastAPI exposes a dependency as the
    `.call` attribute on its dependant node. A closure would put the string in
    `__closure__`, retrievable only by cell position — so refactoring the factory
    would silently break the one test whose job is catching refactors.

    An empty `permissions` means "authenticated, no permission required". That is
    a real declaration, not an absence of one: assertion A5 in the security test
    refuses it for mutations, so it cannot become a universal escape hatch.
    """

    permissions: tuple[str, ...]

    @property
    def is_authenticated_only(self) -> bool:
        return not self.permissions

    async def __call__(
        self,
        request: Request,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: CurrentUser,
    ) -> Principal:
        # FastAPI caches sub-dependencies per request, so `get_current_user` runs
        # once even when a handler declares both this and `CurrentUser`. That also
        # fixes the ordering: an unauthenticated caller gets 401 from there before
        # any 403 can be raised here.
        granted = await authz.effective_permissions(db, user.id)

        missing = [key for key in self.permissions if key not in granted]
        if missing:
            await self._deny(request, db, user, missing)

        return Principal(user=user, permissions=granted)

    async def _deny(
        self, request: Request, db: AsyncSession, user: User, missing: list[str]
    ) -> None:
        logger.warning(
            "permission_denied",
            user_id=str(user.id),
            required=list(self.permissions),
            missing=missing,
            path=request.url.path,
            method=request.method,
        )

        # Reads are not audited. A UI that hides a button still polls, so auditing
        # denied GETs fills `audit_log` with noise generated by the client working
        # as designed. A denied *write* is somebody trying to do something.
        if request.method != "GET":
            await audit.write_audit(
                db,
                action=audit.PERMISSION_DENIED,
                entity_type="route",
                actor_id=user.id,
                after={
                    "required": list(self.permissions),
                    "missing": missing,
                    "method": request.method,
                    "path": request.url.path,
                },
                ip=client_ip(request),
                user_agent=user_agent(request),
            )
            # Commit before raising. `get_db` rolls back on any exception, so the
            # row describing the denial would otherwise be erased by the very
            # error that recorded it — the same defect found three times in the
            # Phase 2 login, OTP, and refresh paths.
            await db.commit()

        raise PermissionDeniedError("You do not have permission to do that.")


def require_permission(*permissions: str) -> PermissionRequirement:
    """Declare what a route requires. All listed permissions must be held.

    Validated against the registry here rather than at request time: a typo like
    `user:mange` would otherwise be a permanent, silent 403 that nobody notices
    until someone files a ticket. This way the application refuses to start.

    Phase 4 adds a `project_param` argument for layer 2 and a `mode="any"` for
    routes satisfied by one of several permissions. Both are omitted now because
    no route needs them, and adding an optional keyword later changes no existing
    call site.
    """
    unknown = sorted(set(permissions) - PERMISSION_KEYS)
    if unknown:
        raise ValueError(f"unknown permission(s): {', '.join(unknown)}")
    if not permissions:
        raise ValueError(
            "require_permission() needs at least one permission; "
            "use require_authenticated() for identity-only routes"
        )
    return PermissionRequirement(tuple(permissions))


def require_authenticated() -> PermissionRequirement:
    """Signed in, but no particular permission — `/users/me`, `/auth/logout-all`.

    Exists so that "this route was considered and needs no permission" is written
    down, rather than being indistinguishable from "nobody thought about it".
    """
    return PermissionRequirement(())


# The ready-made annotation for identity-only routes. Permission-bearing routes
# spell theirs out at the router, so the requirement reads next to the handler:
#     AdminUsers = Annotated[Principal, Depends(require_permission("user:manage"))]
AuthenticatedPrincipal = Annotated[Principal, Depends(require_authenticated())]
