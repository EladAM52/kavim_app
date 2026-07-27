"""Enumerations shared by models, schemas, services, and the permission registry.

Lives in ``core`` rather than ``models`` because it is the bottom of the
dependency graph: ``core.permissions`` needs `RoleKey`, and ``core`` may not
import from ``models`` under the layering contract in ``.importlinter``.

Stored as `VARCHAR` with a `CHECK` constraint rather than a native PostgreSQL
`ENUM` type. Native enums require `ALTER TYPE` to add a value, which cannot run
inside a transaction on older servers and makes an expand/contract deploy
awkward. A `CHECK` constraint gives the same integrity for a plain ALTER.
"""

from __future__ import annotations

from enum import StrEnum


class Locale(StrEnum):
    HE = "he"
    EN = "en"


# ── identity ──────────────────────────────────────────────────────────────
class RoleKey(StrEnum):
    """Seeded global roles (SPEC §8.4). The role → permission matrix is
    editable at runtime; these keys are not."""

    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    LINE_MANAGER = "LINE_MANAGER"
    SHIFT_SUPERVISOR = "SHIFT_SUPERVISOR"
    WORKER = "WORKER"
    VIEWER = "VIEWER"


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class AuthProvider(StrEnum):
    """Present from day 1 so Entra ID SSO is an added path rather than a
    migration (SPEC §14 R7)."""

    PASSWORD = "password"
    ENTRA_ID = "entra_id"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class OtpPurpose(StrEnum):
    REGISTRATION = "registration"
    PHONE_VERIFY = "phone_verify"
    LOGIN_MFA = "login_mfa"


class OtpChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class TokenRevokeReason(StrEnum):
    ROTATED = "rotated"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    REUSE_DETECTED = "reuse_detected"
    ADMIN_FORCE = "admin_force"
    PASSWORD_RESET = "password_reset"


# ── projects and boards ───────────────────────────────────────────────────
class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ProjectPermissionLevel(StrEnum):
    """Layer 2 of the authorization model. Effective permission is the
    intersection of this and the user's global role (SPEC §8.4)."""

    OWNER = "owner"
    EDITOR = "editor"
    COMMENTER = "commenter"
    VIEWER = "viewer"


class ColumnType(StrEnum):
    """The 14 supported column types (SPEC §7.3).

    Adding one means touching `modules/projects/columns.py` for validation and
    `components/board/cells/` for the editor — see CLAUDE.md.
    """

    STATUS = "status"
    PERSON = "person"
    DATE = "date"
    TIMELINE = "timeline"
    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    RATING = "rating"
    FILE = "file"
    LINK = "link"
    EMAIL = "email"
    PHONE = "phone"


class DependencyType(StrEnum):
    BLOCKS = "blocks"


# ── notifications ─────────────────────────────────────────────────────────
class NotificationEvent(StrEnum):
    """Every trigger is independently toggleable per user per channel (FR-704)."""

    INVITATION = "invitation"
    OTP_CODE = "otp_code"
    PASSWORD_RESET = "password_reset"
    PHONE_VERIFY = "phone_verify"
    TASK_ASSIGNED = "task_assigned"
    COMMENT_MENTION = "comment_mention"
    COMMENT_ADDED = "comment_added"
    STATUS_CHANGED = "status_changed"
    DUE_REMINDER = "due_reminder"
    TASK_OVERDUE = "task_overdue"
    PROJECT_SHARED = "project_shared"
    DAILY_DIGEST = "daily_digest"
    ACCOUNT_LOCKED = "account_locked"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    IN_APP = "in_app"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    FAILED = "failed"
    SKIPPED_PREFERENCE = "skipped_preference"
    SKIPPED_OPTED_OUT = "skipped_opted_out"
    SKIPPED_UNVERIFIED = "skipped_unverified"
    DEFERRED_QUIET_HOURS = "deferred_quiet_hours"
    # Distinct from quiet hours on purpose. Both mean "not sent yet, on purpose",
    # but an admin seeing one should check the user's schedule and the other should
    # check the Gmail ceiling (FR-714). Collapsing them sends people to the wrong
    # place.
    DEFERRED_QUOTA = "deferred_quota"


# ── files ─────────────────────────────────────────────────────────────────
class AttachmentStatus(StrEnum):
    """Uploads are presigned direct-to-storage, so a row exists before the
    object does. `PENDING` rows with no confirmation are swept by maintenance."""

    PENDING = "pending"
    READY = "ready"
    INFECTED = "infected"
    FAILED = "failed"


class ScanStatus(StrEnum):
    SKIPPED = "skipped"
    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
