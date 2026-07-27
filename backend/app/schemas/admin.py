"""Administration request/response models (SPEC §6.4, §9.3).

Every id is a `str`, not a `UUID`, for the same reason every outbound email is a
`str`: these are for rendering, and a serialisation quirk on a response should
never be able to turn a valid row into a 500.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import EmailStr, Field

from app.core.enums import InvitationStatus, Locale, RoleKey, UserStatus
from app.schemas.common import SchemaBase


# ══════════════════════════════════════════════════════════════════════════
#  roles and permissions (FR-203)
# ══════════════════════════════════════════════════════════════════════════
class PermissionRow(SchemaBase):
    """One grantable permission.

    `resource` is the RoleMatrix's grouping key, not the key's prefix — three
    permissions carry `structure` so they render under one heading.
    """

    key: str
    resource: str
    description_he: str
    description_en: str


class RoleRow(SchemaBase):
    """A role and everything it currently grants.

    `user_count` exists so the matrix screen can say *"this affects 12 people"*
    before an administrator unticks a box. Revoking a permission from LINE_MANAGER
    is not a change to a row in a table; it is a change to everyone holding it,
    and the UI should say so.
    """

    id: str
    key: RoleKey
    label_he: str
    label_en: str
    rank: int
    is_system: bool
    permission_keys: list[str]
    user_count: int


class RolePermissionsUpdate(SchemaBase):
    """The complete new permission set for a role — not a delta.

    A whole-set PUT rather than add/remove endpoints because FR-203 requires the
    save to be atomic: the screen is a grid of checkboxes and the administrator
    means "this is the state I want", not "apply these three edits in order".
    """

    permission_keys: list[str] = Field(max_length=200)


# ══════════════════════════════════════════════════════════════════════════
#  users (FR-201, FR-202, FR-206, FR-207)
# ══════════════════════════════════════════════════════════════════════════
class AdminUserRow(SchemaBase):
    id: str
    email: str
    full_name: str
    status: UserStatus
    locale: Locale
    roles: list[str]
    last_login_at: datetime | None
    created_at: datetime
    # Non-null means the account is locked out right now (FR-109).
    locked_until: datetime | None


class AdminUserUpdate(SchemaBase):
    """Role and status. Nothing else — an admin does not edit someone's name here.

    Both optional; sending neither is a 422 rather than a silent no-op, so a
    client with a bug finds out.
    """

    role_key: RoleKey | None = None
    status: UserStatus | None = None


class ColumnVerdict(SchemaBase):
    """Layer 3 for one column: may this user write it, and why (FR-205)."""

    key: str
    label_he: str
    label_en: str
    editable_by_roles: list[str]
    editable: bool


class EffectivePermissionsTrace(SchemaBase):
    """FR-210 — "why can this person edit this?", answered layer by layer.

    Deliberately shows the *inputs* as well as the result. An administrator
    looking at this screen already knows the effective set is wrong; what they
    need is which of the three layers produced it.
    """

    user_id: str
    email: str
    roles: list[str]
    layer1_role_permissions: list[str]
    layer2_project_level: str | None
    layer2_level_permissions: list[str]
    effective: list[str]
    layer3_columns: list[ColumnVerdict]
    computed_at: datetime


# ══════════════════════════════════════════════════════════════════════════
#  invitations (FR-101, FR-111)
# ══════════════════════════════════════════════════════════════════════════
class InvitationCreate(SchemaBase):
    # EmailStr inbound: catching a typo here is the difference between an
    # invitation and a message sent to nobody, discovered a week later.
    email: EmailStr
    role_key: RoleKey
    project_ids: list[str] = Field(default_factory=list, max_length=50)


class InvitationRow(SchemaBase):
    """Never carries the raw token.

    The token is the credential. It exists in exactly one place — the emailed
    link — and putting it in a list response would let anyone with `user:invite`
    accept an invitation addressed to someone else.
    """

    id: str
    email: str
    role_key: str
    status: InvitationStatus
    project_ids: list[str]
    invited_by_name: str | None
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


# ══════════════════════════════════════════════════════════════════════════
#  audit log (FR-208)
# ══════════════════════════════════════════════════════════════════════════
class AuditRow(SchemaBase):
    id: int
    action: str
    entity_type: str
    entity_id: str | None
    actor_id: str | None
    actor_name: str | None
    project_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    request_id: str | None
    created_at: datetime
