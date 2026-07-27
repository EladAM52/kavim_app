"""Audit writing (CLAUDE.md rule 6, NFR-15).

Called **inside the same transaction as the change it describes**, so a mutation
cannot exist without its audit row. No flush, no commit here — the caller owns the
transaction, and committing early would break exactly that guarantee.

The table is append-only, enforced by a database trigger rather than a `GRANT`:
in development the application connects as the table owner, and an owner always
retains full privileges, so a grant alone would not hold. The trigger holds for
every role including buggy ORM code, which is the real threat model.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_var
from app.models.audit import AuditLog

# ── auth actions ──────────────────────────────────────────────────────────
# String constants rather than free text at call sites, so the audit log stays
# greppable and a typo cannot silently create a new action name.
#
# ruff's S105 flags every name containing PASSWORD as a possible hardcoded
# credential. These are audit action identifiers, so the rule is suppressed for
# the block rather than argued with.
# ruff: noqa: S105
INVITATION_CREATED: Final = "invitation.created"
INVITATION_CONSUMED: Final = "invitation.consumed"
INVITATION_REVOKED: Final = "invitation.revoked"
OTP_REQUESTED: Final = "otp.requested"
OTP_VERIFIED: Final = "otp.verified"
OTP_FAILED: Final = "otp.failed"
USER_REGISTERED: Final = "user.registered"
LOGIN_SUCCEEDED: Final = "auth.login_succeeded"
LOGIN_FAILED: Final = "auth.login_failed"
ACCOUNT_LOCKED: Final = "auth.account_locked"
LOGOUT: Final = "auth.logout"
LOGOUT_ALL: Final = "auth.logout_all"
TOKEN_REFRESHED: Final = "auth.token_refreshed"
TOKEN_REUSE_DETECTED: Final = "auth.token_reuse_detected"
PASSWORD_RESET_REQUESTED: Final = "auth.password_reset_requested"
PASSWORD_RESET_COMPLETED: Final = "auth.password_reset_completed"

# Values that must never reach the audit log even if a caller passes them.
_REDACTED: Final = frozenset(
    {"password", "password_hash", "token", "token_hash", "code", "code_hash", "secret"}
)


def _scrub(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop secrets from an audit payload.

    A defence in depth, not a substitute for care at the call site: the audit log
    is readable by auditors and retained for 24 months, so a password that lands
    in it is a long-lived disclosure.
    """
    if data is None:
        return None
    return {
        key: ("[redacted]" if key.lower() in _REDACTED else value) for key, value in data.items()
    }


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Record one mutation. Caller owns the transaction.

    `actor_id` is nullable on purpose: an unauthenticated event still needs
    recording. A failed login has no actor by definition, and skipping it for
    lack of one would lose precisely the events an investigation needs.
    """
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        before=_scrub(before),
        after=_scrub(after),
        ip=ip,
        user_agent=user_agent,
        # Correlates this row with the HTTP request and any Celery task it spawned.
        request_id=request_id_var.get(),
    )
    db.add(row)
    return row
