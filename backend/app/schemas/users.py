"""Self-service profile models (SPEC §6.3, §9.3).

`MeResponse` is what the SPA renders its shell from and what feeds the
`usePermission` hook, so it carries the caller's *effective* permissions rather
than just their roles — the frontend must never have to derive one from the other,
because a client-side derivation is a second permission model that can disagree
with the server's.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime

from pydantic import Field, field_validator

from app.core.enums import Locale, UserStatus
from app.schemas.common import SchemaBase, normalize_israeli_phone


class MeResponse(SchemaBase):
    """The signed-in user's own record.

    A superset of `UserIdentity`, which stays as it is because it is embedded in
    `TokenResponse` and a login response has no business carrying a timezone.
    """

    id: str
    # str, not EmailStr — validating an outbound address can only turn a readable
    # row into a 500. See the note in `schemas/auth.py`.
    email: str
    full_name: str
    phone: str | None
    avatar_url: str | None
    locale: Locale
    timezone: str
    status: UserStatus
    roles: list[str]
    permissions: list[str]
    last_login_at: datetime | None


class MeUpdate(SchemaBase):
    """Everything a user may change about themselves — and nothing else.

    No `email`, `status`, or `role`. `extra="forbid"` turns an attempt at any of
    them into a 422 rather than a silently ignored field, so a client that thinks
    it changed its own role finds out immediately.

    Every field is optional and `None` means "leave alone", except `phone`, where
    the user genuinely may want to clear it. That ambiguity is resolved by
    `phone_cleared`: sending it removes the number, which is different from
    omitting `phone` entirely.
    """

    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    phone_cleared: bool = False
    locale: Locale | None = None
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, value: str | None) -> str | None:
        return normalize_israeli_phone(value)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        """Reject an unknown zone here, not at the first render.

        An invalid timezone stored on a user does not fail until something tries
        to localise a timestamp for them — which is a page they cannot load, far
        from the settings form that caused it.
        """
        if value is None:
            return None
        if value not in zoneinfo.available_timezones():
            raise ValueError(f"unknown timezone: {value}")
        return value
