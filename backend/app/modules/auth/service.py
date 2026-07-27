"""Registration, login, and refresh rotation (SPEC §8.1, §8.2).

The three security properties here, and where each is enforced:

* **No user enumeration.** `login` runs an argon2 verification even when no user
  exists (`waste_password_time`), and returns one message for every failure. A
  "no such account" response, or a measurably faster one, is a user list.
* **Lockout is database-backed.** `users.failed_login_count` and `locked_until`,
  so the control survives a Redis restart.
* **Refresh reuse revokes the family.** Presenting an already-rotated token means
  one was stolen and replayed, so every token in the chain dies at once. That
  turns theft into a single-use event rather than persistent access.

**Why there are explicit `db.commit()` calls before some `raise` statements.**
`get_db` rolls back on any exception, which is right for domain writes and wrong
for security bookkeeping: the failed-login counter, the account lock, and a reuse
revocation all describe *the very failure being raised*. Rolled back, the counter
never reaches its threshold, the lock never persists, and reuse detection detects
theft and then does nothing. So those writes are committed before the error
propagates. Every such commit is commented at its site; there are no others.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import (
    AuthProvider,
    InvitationStatus,
    Locale,
    NotificationEvent,
    ProjectPermissionLevel,
    TokenRevokeReason,
    UserStatus,
)
from app.core.exceptions import AccountLockedError, AuthenticationError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    TokenError,
    create_access_token,
    decode_registration_ticket,
    hash_password,
    hash_token,
    new_refresh_token,
    new_token_family,
    password_needs_rehash,
    refresh_expiry,
    validate_password_strength,
    verify_password,
    waste_password_time,
)
from app.core.time import utc_now
from app.models.auth import RefreshToken
from app.models.project import ProjectMember
from app.models.role import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.modules.audit import service as audit
from app.modules.auth import invitations as invitations_mod
from app.modules.notifications import service as notifications
from app.schemas.auth import RegisterRequest, UserIdentity

logger = get_logger(__name__)

# One message for every login failure. Distinguishing "no such user" from "wrong
# password" hands an attacker a validated address list.
_LOGIN_FAILED = "Email or password is incorrect."


# ══════════════════════════════════════════════════════════════════════════
#  identity
# ══════════════════════════════════════════════════════════════════════════
async def load_role_keys(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = await db.scalars(
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    return sorted(str(key) for key in rows)


async def load_global_permissions(db: AsyncSession, user_id: uuid.UUID) -> frozenset[str]:
    """Layer 1 of the authorization model — the union across the user's roles.

    Layers 2 and 3 (project membership, column rules) narrow this per resource and
    are applied at the point of use, not here.
    """
    rows = await db.scalars(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    return frozenset(str(key) for key in rows)


async def build_identity(db: AsyncSession, user: User) -> UserIdentity:
    """The payload the SPA renders its shell from.

    `permissions` hides buttons; it decides nothing. Every mutation re-checks
    server-side (CLAUDE.md rule 2).
    """
    roles = await load_role_keys(db, user.id)
    permissions = await load_global_permissions(db, user.id)
    return UserIdentity(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        locale=user.locale,
        roles=roles,
        permissions=sorted(permissions),
    )


# ══════════════════════════════════════════════════════════════════════════
#  refresh tokens
# ══════════════════════════════════════════════════════════════════════════
async def issue_refresh_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[RefreshToken, str]:
    """Returns ``(row, raw_token)``. Only the digest is stored."""
    raw, digest = new_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=digest,
        family_id=family_id or new_token_family(),
        parent_id=parent_id,
        ip=ip,
        user_agent=user_agent,
        expires_at=refresh_expiry(),
    )
    db.add(row)
    return row, raw


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID, reason: TokenRevokeReason) -> int:
    """Revoke every live token in a rotation chain. Returns how many."""
    now = utc_now()
    rows = list(
        await db.scalars(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    )
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = reason
    return len(rows)


async def rotate_refresh_token(
    db: AsyncSession,
    *,
    raw_token: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, str]:
    """Exchange a refresh token for a new pair. Returns ``(user, access, raw_refresh)``.

    Reuse detection is the whole point. A token already marked revoked with reason
    `rotated` means someone replayed a value that has already been spent — either
    the legitimate client raced itself, or a token was stolen. Both are handled
    the same way, because the API cannot tell them apart and the safe reading is
    theft: revoke the entire family and force a fresh login.
    """
    presented = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if presented is None:
        raise AuthenticationError("Session expired. Please sign in again.")

    if presented.revoked_at is not None:
        revoked_count = await _revoke_family(
            db, presented.family_id, TokenRevokeReason.REUSE_DETECTED
        )
        await audit.write_audit(
            db,
            action=audit.TOKEN_REUSE_DETECTED,
            entity_type="refresh_token",
            entity_id=presented.id,
            actor_id=presented.user_id,
            after={
                "family_id": str(presented.family_id),
                "original_reason": presented.revoked_reason,
                "tokens_revoked": revoked_count,
            },
            ip=ip,
            user_agent=user_agent,
        )
        # The user is told, because a replay they did not cause is the one signal
        # that their session was stolen.
        user = await db.get(User, presented.user_id)
        if user is not None:
            await notifications.queue_email_to_address(
                db,
                event=NotificationEvent.ACCOUNT_LOCKED,
                email=user.email,
                locale=str(user.locale),
                context={"reason": "token_reuse"},
                entity_type="user",
                entity_id=user.id,
            )
        # Commit the revocation before raising, for the same reason as the login
        # counters: `get_db` rolls back on exception, and a reuse detection whose
        # revocation is rolled back detects theft and then does nothing about it.
        await db.commit()
        logger.warning(
            "refresh_reuse_detected",
            family_id=str(presented.family_id),
            revoked=revoked_count,
        )
        raise AuthenticationError("Session invalidated for security reasons. Please sign in again.")

    if presented.expires_at <= utc_now():
        raise AuthenticationError("Session expired. Please sign in again.")

    user = await db.get(User, presented.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Session expired. Please sign in again.")

    now = utc_now()
    presented.revoked_at = now
    presented.revoked_reason = TokenRevokeReason.ROTATED
    presented.last_used_at = now

    _, raw_refresh = await issue_refresh_token(
        db,
        user_id=user.id,
        family_id=presented.family_id,
        parent_id=presented.id,
        ip=ip,
        user_agent=user_agent,
    )

    roles = await load_role_keys(db, user.id)
    access = create_access_token(user.id, email=user.email, roles=roles)
    return user, access, raw_refresh


async def revoke_single_token(db: AsyncSession, raw_token: str) -> None:
    """Logout. Unknown tokens are ignored — a logout must always appear to work."""
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = utc_now()
    row.revoked_reason = TokenRevokeReason.LOGOUT


async def revoke_all_user_tokens(
    db: AsyncSession, user_id: uuid.UUID, reason: TokenRevokeReason
) -> int:
    now = utc_now()
    rows = list(
        await db.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    )
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = reason
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════
#  registration
# ══════════════════════════════════════════════════════════════════════════
async def register_from_ticket(
    db: AsyncSession,
    payload: RegisterRequest,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, str]:
    """Create the account the invitation describes. Returns ``(user, access, refresh)``.

    Everything below happens in the caller's single transaction: the user row, the
    role assignment, the project memberships, the invitation transition, and the
    audit row commit together or not at all.
    """
    try:
        invitation_id, ticket_email = decode_registration_ticket(payload.registration_ticket)
    except TokenError as exc:
        raise AuthenticationError("This registration link expired. Start again.") from exc

    invitation = await invitations_mod.load_invitation_by_id(db, invitation_id)

    # Belt and braces: the ticket is signed, so this cannot normally differ. It is
    # checked anyway because the consequence of it differing — an account bound to
    # the wrong address — is the exact thing §8.1 exists to prevent.
    if invitation.email.lower() != ticket_email.lower():
        logger.error(
            "registration_ticket_email_mismatch",
            invitation_id=str(invitation_id),
        )
        raise AuthenticationError("This registration link is not valid. Start again.")

    if problems := validate_password_strength(payload.password):
        raise ValidationError(
            "The password does not meet the requirements.",
            errors=[{"field": "password", "message": problem} for problem in problems],
        )

    user = User(
        # From the invitation, never from the form.
        email=invitation.email,
        full_name=payload.full_name,
        phone=payload.phone,
        locale=payload.locale,
        status=UserStatus.ACTIVE,
        auth_provider=AuthProvider.PASSWORD,
        password_hash=hash_password(payload.password),
        password_changed_at=utc_now(),
    )
    db.add(user)
    await db.flush()

    db.add(UserRole(user_id=user.id, role_id=invitation.role_id, assigned_by=invitation.invited_by))

    for project_id in invitation.project_ids:
        db.add(
            ProjectMember(
                project_id=project_id,
                user_id=user.id,
                # Invited members start as editors; a manager narrows or widens it
                # afterwards. Defaulting to owner would hand a new worker the
                # ability to delete the board.
                permission_level=ProjectPermissionLevel.EDITOR,
                added_by=invitation.invited_by,
            )
        )

    invitations_mod.mark_consumed(invitation, user.id)

    await audit.write_audit(
        db,
        action=audit.USER_REGISTERED,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        after={
            "email": user.email,
            "role_id": str(invitation.role_id),
            "invitation_id": str(invitation.id),
            "projects": [str(pid) for pid in invitation.project_ids],
        },
        ip=ip,
        user_agent=user_agent,
    )
    await audit.write_audit(
        db,
        action=audit.INVITATION_CONSUMED,
        entity_type="invitation",
        entity_id=invitation.id,
        actor_id=user.id,
        before={"status": InvitationStatus.PENDING.value},
        after={"status": InvitationStatus.CONSUMED.value},
        ip=ip,
        user_agent=user_agent,
    )

    _, raw_refresh = await issue_refresh_token(db, user_id=user.id, ip=ip, user_agent=user_agent)
    roles = await load_role_keys(db, user.id)
    access = create_access_token(user.id, email=user.email, roles=roles)

    logger.info("user_registered", user_id=str(user.id))
    return user, access, raw_refresh


# ══════════════════════════════════════════════════════════════════════════
#  login
# ══════════════════════════════════════════════════════════════════════════
async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str, str]:
    """Verify credentials. Returns ``(user, access, raw_refresh)``.

    Every failure path raises `AuthenticationError` with the same message, and the
    "no such user" branch still performs a hash verification so the timing
    matches. A lockout is the one exception: the user is told, because silently
    rejecting a correct password is worse than confirming the account exists to
    someone who already knows the address.
    """
    user = await db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))

    if user is None:
        waste_password_time()
        await audit.write_audit(
            db,
            action=audit.LOGIN_FAILED,
            entity_type="user",
            after={"email": email, "reason": "no_such_user"},
            ip=ip,
            user_agent=user_agent,
        )
        # Commit the failure bookkeeping before raising. `get_db` rolls back on
        # any exception, so without this the counter increment, the lock, and the
        # audit row are all undone by the very error that recorded them — and
        # lockout silently never happens. See the note above `login`.
        await db.commit()
        raise AuthenticationError(_LOGIN_FAILED)

    now = utc_now()
    if user.locked_until is not None and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds())
        raise AccountLockedError(
            "This account is temporarily locked after repeated failed sign-ins.",
            extra={"retry_after_seconds": remaining},
        )

    if user.password_hash is None or not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        reason = "bad_password"

        if user.failed_login_count >= settings.LOGIN_MAX_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            user.failed_login_count = 0
            reason = "locked_out"
            await audit.write_audit(
                db,
                action=audit.ACCOUNT_LOCKED,
                entity_type="user",
                entity_id=user.id,
                actor_id=user.id,
                after={"locked_until": user.locked_until.isoformat()},
                ip=ip,
                user_agent=user_agent,
            )
            await notifications.queue_email_to_address(
                db,
                event=NotificationEvent.ACCOUNT_LOCKED,
                email=user.email,
                locale=str(user.locale),
                context={"reason": "failed_logins", "minutes": settings.LOGIN_LOCKOUT_MINUTES},
                entity_type="user",
                entity_id=user.id,
            )
            logger.warning("account_locked", user_id=str(user.id))

        await audit.write_audit(
            db,
            action=audit.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id,
            actor_id=user.id,
            after={"reason": reason, "failed_count": user.failed_login_count},
            ip=ip,
            user_agent=user_agent,
        )
        # Commit the failure bookkeeping before raising. `get_db` rolls back on
        # any exception, so without this the counter increment, the lock, and the
        # audit row are all undone by the very error that recorded them — and
        # lockout silently never happens. See the note above `login`.
        await db.commit()
        raise AuthenticationError(_LOGIN_FAILED)

    if not user.is_active:
        # Same message as a wrong password: whether an account is deactivated is
        # not something an unauthenticated caller needs to learn.
        await audit.write_audit(
            db,
            action=audit.LOGIN_FAILED,
            entity_type="user",
            entity_id=user.id,
            actor_id=user.id,
            after={"reason": "inactive", "status": user.status.value},
            ip=ip,
            user_agent=user_agent,
        )
        # Commit the failure bookkeeping before raising. `get_db` rolls back on
        # any exception, so without this the counter increment, the lock, and the
        # audit row are all undone by the very error that recorded them — and
        # lockout silently never happens. See the note above `login`.
        await db.commit()
        raise AuthenticationError(_LOGIN_FAILED)

    # Transparently upgrade a hash written with older cost parameters, so raising
    # them later does not require a password reset for everyone.
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip

    _, raw_refresh = await issue_refresh_token(db, user_id=user.id, ip=ip, user_agent=user_agent)
    roles = await load_role_keys(db, user.id)
    access = create_access_token(user.id, email=user.email, roles=roles)

    await audit.write_audit(
        db,
        action=audit.LOGIN_SUCCEEDED,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    logger.info("login_succeeded", user_id=str(user.id))
    return user, access, raw_refresh


def default_locale_for(user: User | None) -> Locale:
    return user.locale if user is not None else Locale(settings.DEFAULT_LOCALE)
