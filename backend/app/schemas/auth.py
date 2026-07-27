"""Auth request/response models (SPEC §8.1, §9.3).

These are the OpenAPI contract: `npm run api:types` generates the frontend's
types from the schema FastAPI derives from this file. A field renamed here is a
compile error in the frontend, which is the point.

Three conventions worth stating, because they recur:

* **No response model carries a secret.** Refresh tokens travel in an httpOnly
  cookie, never in a body, so an XSS payload has nothing to read.
* **Requests never carry an email where one can be looked up instead.** The
  registration flow proves this: the account's address comes from the invitation
  row, so `RegisterRequest` has no email field at all.
* **`EmailStr` on the way in, plain `str` on the way out.** Validating an inbound
  address catches a typo before an invitation is sent to nobody. Validating an
  *outbound* one re-checks a value the database already holds, so the only thing
  it can achieve is turning a readable row into a 500 — which is exactly what
  happened the first time this file used `EmailStr` on responses and met an
  address at a special-use domain.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.enums import Locale


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ══════════════════════════════════════════════════════════════════════════
#  invitation → OTP → register
# ══════════════════════════════════════════════════════════════════════════
class InvitationPreview(_Base):
    """What an invitee is shown on landing, before proving mailbox control.

    The email is echoed so the form can display it **read-only** — it is not an
    input. Nothing sensitive is here: possession of the link is already required
    to reach this response.
    """

    # str, not EmailStr — see the module docstring.
    email: str
    role_key: str
    role_label: str
    locale: Locale
    expires_at: datetime
    invited_by_name: str


class OtpRequestPayload(_Base):
    """The invitation token, and nothing else.

    No email field, deliberately. The code goes to the address on the invitation,
    which is what makes it proof of mailbox control rather than a formality
    (SPEC §8.1).
    """

    token: str = Field(min_length=16, max_length=128)


class OtpVerifyPayload(_Base):
    token: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class RegistrationTicket(_Base):
    """Issued after OTP verification. Scoped `register`, valid 15 minutes.

    From here on the raw invitation token is never transmitted again.
    """

    registration_ticket: str
    email: str
    expires_in_seconds: int


class RegisterRequest(_Base):
    """No email field. The address comes from the invitation the ticket names —
    that is what stops a forwarded invitation being redeemed by someone else."""

    registration_ticket: str
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=10, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    locale: Locale = Locale.HE

    @field_validator("phone")
    @classmethod
    def _normalize_israeli_phone(cls, value: str | None) -> str | None:
        """Store E.164. `050-123-4567` and `+972501234567` are the same number,
        and storing both shapes makes every later comparison unreliable."""
        if not value:
            return None
        digits = "".join(char for char in value if char.isdigit() or char == "+")
        if digits.startswith("0"):
            return f"+972{digits[1:]}"
        if not digits.startswith("+"):
            return f"+{digits}"
        return digits


# ══════════════════════════════════════════════════════════════════════════
#  login / session
# ══════════════════════════════════════════════════════════════════════════
class LoginRequest(_Base):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(_Base):
    """The access token only.

    The refresh token is set as an httpOnly cookie by the endpoint and is
    deliberately absent from this body (SPEC §8.2): a token the page's JavaScript
    can read is a token an XSS payload can steal.
    """

    access_token: str
    # Not a credential — the OAuth 2 scheme name the client puts in the
    # Authorization header.
    token_type: str = "bearer"  # noqa: S105
    expires_in_seconds: int
    user: UserIdentity


class UserIdentity(_Base):
    """Enough to render the shell without a second round trip.

    `permissions` is a UX affordance for hiding buttons. The server re-checks on
    every mutation — see CLAUDE.md rule 2.
    """

    id: str
    email: str
    full_name: str
    locale: Locale
    roles: list[str]
    permissions: list[str]


# ══════════════════════════════════════════════════════════════════════════
#  password reset
# ══════════════════════════════════════════════════════════════════════════
class PasswordResetRequestPayload(_Base):
    email: EmailStr


class PasswordResetConfirmPayload(_Base):
    token: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=10, max_length=200)


# ══════════════════════════════════════════════════════════════════════════
#  generic
# ══════════════════════════════════════════════════════════════════════════
class AcceptedResponse(_Base):
    """A `202` for endpoints that must not reveal whether anything happened.

    OTP request and password reset both return this whether or not the address
    exists, because a different response for a known address is a user
    enumeration oracle (SPEC §8.3).
    """

    status: str = "accepted"
    detail: str | None = None


class MessageResponse(_Base):
    detail: str


# Resolve the forward reference from TokenResponse.
TokenResponse.model_rebuild()
