"""Auth endpoints (SPEC §8.1, §9.3).

The refresh token lives in an httpOnly cookie scoped to `/api/v1/auth`, so it is
sent on refresh and logout and on nothing else. `SameSite=Strict` plus that path
scope is what keeps every other endpoint out of CSRF reach — they all authenticate
with a bearer header instead, which a cross-site form cannot set.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import settings
from app.core.database import get_db
from app.core.enums import Locale, TokenRevokeReason
from app.core.exceptions import AuthenticationError, BadRequestError
from app.core.logging import get_logger
from app.core.security import create_registration_ticket
from app.modules.audit import service as audit
from app.modules.auth import invitations as invitations_mod
from app.modules.auth import otp as otp_mod
from app.modules.auth import passwords, service
from app.modules.auth.dependencies import CurrentUser, client_ip, user_agent
from app.schemas.auth import (
    AcceptedResponse,
    InvitationPreview,
    LoginRequest,
    MessageResponse,
    OtpRequestPayload,
    OtpVerifyPayload,
    PasswordResetConfirmPayload,
    PasswordResetRequestPayload,
    RegisterRequest,
    RegistrationTicket,
    TokenResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "kavim_refresh"
_REGISTRATION_TICKET_TTL_SECONDS = 15 * 60

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        raw_token,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 24 * 3600,
        httponly=True,
        # Secure is skipped in development because localhost is plain HTTP and the
        # cookie would simply never be stored. Production is HTTPS-only (NFR).
        secure=settings.is_production,
        samesite="strict",
        # Scoped so the cookie is not attached to any endpoint that does not need it.
        path=f"{settings.API_PREFIX}/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path=f"{settings.API_PREFIX}/auth",
    )


def _accept_language(request: Request) -> Locale:
    """Locale for outbound mail to someone who has no account yet.

    Once a user exists their stored preference wins; before that, the browser's
    header is the only signal available.
    """
    header = (request.headers.get("Accept-Language") or "").lower()
    return Locale.EN if header.startswith("en") else Locale(settings.DEFAULT_LOCALE)


# ══════════════════════════════════════════════════════════════════════════
#  invitation → OTP → register
# ══════════════════════════════════════════════════════════════════════════
@router.get(
    "/invitations/{token}",
    response_model=InvitationPreview,
    summary="Validate an invitation link",
)
async def read_invitation(token: str, db: DbSession) -> InvitationPreview:
    """Returns the invited address **read-only**, for display on the landing screen.

    `410` when the invitation is expired, consumed, or revoked (FR-102).
    """
    invitation = await invitations_mod.load_valid_invitation(db, token)
    role, inviter_name = await invitations_mod.build_preview_context(db, invitation)

    return InvitationPreview(
        email=invitation.email,
        role_key=str(role.key),
        role_label=invitations_mod.role_label(role, settings.DEFAULT_LOCALE),
        locale=Locale(settings.DEFAULT_LOCALE),
        expires_at=invitation.expires_at,
        invited_by_name=inviter_name,
    )


@router.post(
    "/otp/request",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a one-time code to the invited address",
)
async def request_otp(
    payload: OtpRequestPayload,
    request: Request,
    db: DbSession,
) -> AcceptedResponse:
    """`202` regardless of what happened downstream.

    The invitation is validated first — a bad token is a `404`/`410`, since the
    caller already had to possess a token to get here. But once the invitation is
    valid, whether the mail actually left is not reported: the outbox may retry for
    a minute, and surfacing that as a failure would push a user into re-requesting
    and burning their rate-limit budget.
    """
    invitation = await invitations_mod.load_valid_invitation(db, payload.token)

    await rate_limit.enforce(rate_limit.OTP_REQUEST, invitation.email)

    await otp_mod.issue_otp(
        db,
        invitation=invitation,
        locale=_accept_language(request),
        request_ip=client_ip(request),
    )
    await audit.write_audit(
        db,
        action=audit.OTP_REQUESTED,
        entity_type="invitation",
        entity_id=invitation.id,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    return AcceptedResponse(detail="A verification code is on its way to the invited address.")


@router.post(
    "/otp/verify",
    response_model=RegistrationTicket,
    summary="Verify the code and receive a registration ticket",
)
async def verify_otp(
    payload: OtpVerifyPayload,
    request: Request,
    db: DbSession,
) -> RegistrationTicket:
    """On success the raw invitation token is never needed again — the returned
    ticket carries the invitation id instead (SPEC §8.1)."""
    invitation = await invitations_mod.load_valid_invitation(db, payload.token)

    await rate_limit.enforce(rate_limit.OTP_VERIFY, f"{invitation.email}:verify")

    try:
        await otp_mod.verify_otp(db, email=invitation.email, code=payload.code)
    except Exception:
        await audit.write_audit(
            db,
            action=audit.OTP_FAILED,
            entity_type="invitation",
            entity_id=invitation.id,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
        raise

    await audit.write_audit(
        db,
        action=audit.OTP_VERIFIED,
        entity_type="invitation",
        entity_id=invitation.id,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )

    return RegistrationTicket(
        registration_ticket=create_registration_ticket(invitation.id, invitation.email),
        email=invitation.email,
        expires_in_seconds=_REGISTRATION_TICKET_TTL_SECONDS,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the account and sign in",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> TokenResponse:
    user, access, raw_refresh = await service.register_from_ticket(
        db, payload, ip=client_ip(request), user_agent=user_agent(request)
    )
    _set_refresh_cookie(response, raw_refresh)
    return TokenResponse(
        access_token=access,
        expires_in_seconds=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=await service.build_identity(db, user),
    )


# ══════════════════════════════════════════════════════════════════════════
#  session
# ══════════════════════════════════════════════════════════════════════════
@router.post("/login", response_model=TokenResponse, summary="Sign in")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> TokenResponse:
    ip = client_ip(request)

    # Per IP *and* per email: per-IP alone lets a botnet spread attempts across
    # addresses, per-email alone lets one host walk a user list.
    if ip:
        await rate_limit.enforce(rate_limit.LOGIN_PER_IP, ip)
    await rate_limit.enforce(rate_limit.LOGIN_PER_EMAIL, payload.email)

    user, access, raw_refresh = await service.login(
        db,
        email=payload.email,
        password=payload.password,
        ip=ip,
        user_agent=user_agent(request),
    )

    # Only on success, so one fumbled password does not eat the quota of someone
    # who then types it correctly.
    await rate_limit.reset(rate_limit.LOGIN_PER_EMAIL, payload.email)
    if ip:
        await rate_limit.reset(rate_limit.LOGIN_PER_IP, ip)

    _set_refresh_cookie(response, raw_refresh)
    return TokenResponse(
        access_token=access,
        expires_in_seconds=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=await service.build_identity(db, user),
    )


@router.post("/refresh", response_model=TokenResponse, summary="Rotate the session")
async def refresh(request: Request, response: Response, db: DbSession) -> TokenResponse:
    """Cookie-authenticated, so it carries no bearer header.

    This is also the app's boot call: the access token is held in memory only, so a
    page reload has nothing until this returns.
    """
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise AuthenticationError("No active session.")

    try:
        user, access, new_refresh = await service.rotate_refresh_token(
            db,
            raw_token=raw_token,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
    except AuthenticationError:
        # Clear the cookie on the way out. Leaving a dead token in the browser
        # means every reload retries it and gets the same failure.
        _clear_refresh_cookie(response)
        raise

    _set_refresh_cookie(response, new_refresh)
    return TokenResponse(
        access_token=access,
        expires_in_seconds=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=await service.build_identity(db, user),
    )


@router.post("/logout", response_model=MessageResponse, summary="Revoke this session")
async def logout(request: Request, response: Response, db: DbSession) -> MessageResponse:
    """Always reports success.

    A logout that can fail leaves a user unsure whether they are signed out, and
    there is nothing useful they could do with the distinction.
    """
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        await service.revoke_single_token(db, raw_token)
    _clear_refresh_cookie(response)
    return MessageResponse(detail="Signed out.")


@router.post(
    "/logout-all",
    response_model=MessageResponse,
    summary="Revoke every session for the signed-in user",
)
async def logout_all(
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: DbSession,
) -> MessageResponse:
    """Requires a bearer token, unlike `/logout`.

    Revoking every device is a destructive action, so it needs proof of an active
    session rather than mere possession of one cookie.
    """
    revoked = await service.revoke_all_user_tokens(
        db, current_user.id, TokenRevokeReason.LOGOUT_ALL
    )
    await audit.write_audit(
        db,
        action=audit.LOGOUT_ALL,
        entity_type="user",
        entity_id=current_user.id,
        actor_id=current_user.id,
        after={"sessions_revoked": revoked},
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    _clear_refresh_cookie(response)
    return MessageResponse(detail=f"Signed out of {revoked} session(s).")


# ══════════════════════════════════════════════════════════════════════════
#  password reset
# ══════════════════════════════════════════════════════════════════════════
@router.post(
    "/password-reset/request",
    response_model=AcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password reset link",
)
async def request_password_reset(
    payload: PasswordResetRequestPayload,
    request: Request,
    db: DbSession,
) -> AcceptedResponse:
    """`202` whether or not the address is registered (SPEC §8.3)."""
    await rate_limit.enforce(rate_limit.PASSWORD_RESET_REQUEST, payload.email)
    await passwords.request_reset(
        db,
        email=payload.email,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    return AcceptedResponse(detail="If that address is registered, a reset link is on its way.")


@router.post(
    "/password-reset/confirm",
    response_model=MessageResponse,
    summary="Set a new password and end every session",
)
async def confirm_password_reset(
    payload: PasswordResetConfirmPayload,
    request: Request,
    response: Response,
    db: DbSession,
) -> MessageResponse:
    await passwords.confirm_reset(
        db,
        raw_token=payload.token,
        new_password=payload.password,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    # Every session died, including whatever this browser held.
    _clear_refresh_cookie(response)
    return MessageResponse(detail="Password updated. Please sign in with your new password.")


# ══════════════════════════════════════════════════════════════════════════
#  deferred
# ══════════════════════════════════════════════════════════════════════════
@router.post(
    "/phone/verify/request",
    include_in_schema=False,
    summary="Deferred with FR-702",
)
async def phone_verify_request() -> None:
    """Declared so the route is not silently absent from the contract in §9.3.

    SMS is deferred (ADR-007, SPEC §6.14.1): no provider is integrated, so there
    is nothing to send. Returns `501` rather than `404` to say "planned, not
    missing".
    """
    raise BadRequestError(
        "Phone verification is not available in this release.",
        extra={"deferred": "FR-702"},
    )
