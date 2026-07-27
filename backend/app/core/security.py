"""Password hashing, token generation, JWTs, and constant-time comparison.

Design rules, all from SPEC §8.3:

* Passwords: **argon2id**. Not bcrypt (72-byte truncation, no memory hardness),
  not PBKDF2 (cheap to attack on GPUs).
* Tokens and OTP codes: only a SHA-256 digest is stored. A database dump yields
  no usable invitation link, reset link, or session token.
* Every secret comparison is constant time, so response timing leaks nothing.
* JWTs are **access tokens only**. Refresh tokens are opaque random strings
  looked up in the database, because a refresh token must be revocable and a
  self-contained JWT cannot be revoked before it expires.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.core.time import utc_now

# Tuned for an interactive login: ~50-100 ms on a modern server. Memory cost is
# the parameter that actually resists GPU cracking, so it carries the weight.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

OTP_LENGTH = 6
TOKEN_BYTES = 32


# ── passwords ─────────────────────────────────────────────────────────────
def normalize_password(password: str) -> str:
    """NFKC-normalize so a password typed with a Hebrew keyboard layout, or with
    composed vs. decomposed Unicode, hashes identically every time."""
    return unicodedata.normalize("NFKC", password)


def hash_password(password: str) -> str:
    return _hasher.hash(normalize_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password. Returns False rather than raising on any failure."""
    try:
        return _hasher.verify(password_hash, normalize_password(password))
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash uses outdated parameters.

    Lets a successful login transparently upgrade an old hash, so raising the
    cost parameters later does not require a password reset for everyone.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


# A password check that runs even when no user exists, so a missing account and
# a wrong password take comparable time and the API does not leak which of the
# two happened (SPEC §8.3, no user enumeration).
_DUMMY_HASH = hash_password("kavim-timing-equalizer-not-a-real-password")


def waste_password_time() -> None:
    """Call on the "user not found" branch of login."""
    verify_password("wrong", _DUMMY_HASH)


def validate_password_strength(password: str) -> list[str]:
    """Return a list of problems; empty means acceptable.

    Length over composition rules, deliberately: a 14-character passphrase beats
    "P@ssw0rd!" in every way that matters, and forced symbol classes push people
    toward predictable substitutions.
    """
    problems: list[str] = []
    normalized = normalize_password(password)

    if len(normalized) < settings.PASSWORD_MIN_LENGTH:
        problems.append(f"must be at least {settings.PASSWORD_MIN_LENGTH} characters")
    if len(normalized) > 200:
        problems.append("must be at most 200 characters")
    if normalized != normalized.strip():
        problems.append("must not start or end with whitespace")
    if normalized and len(set(normalized)) == 1:
        problems.append("must not be a single repeated character")

    return problems


# ── tokens ────────────────────────────────────────────────────────────────
def generate_token(num_bytes: int = TOKEN_BYTES) -> str:
    """URL-safe random token for invitations, password resets, refresh tokens."""
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    """SHA-256 hex digest — what actually gets stored.

    SHA-256 rather than argon2 here on purpose: these tokens are 256 bits of
    entropy, so there is nothing to brute force, and lookup by digest must be an
    indexed equality match. Argon2's per-hash salt would make that impossible.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_otp(length: int = OTP_LENGTH) -> str:
    """A numeric one-time code, zero-padded.

    `randbelow` rather than `random` — the latter is a Mersenne Twister and its
    output is predictable from prior values.
    """
    upper = 10**length
    return str(secrets.randbelow(upper)).zfill(length)


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def constant_time_compare(left: str, right: str) -> bool:
    """Use for every token, digest, and signature comparison."""
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ══════════════════════════════════════════════════════════════════════════
#  JWT
# ══════════════════════════════════════════════════════════════════════════
# `scope` is what stops a token being used outside its purpose. A registration
# ticket must not authenticate API calls, and an access token must not complete a
# registration — so the scope is checked on decode, not merely present.
TokenScope = Literal["access", "register"]

SCOPE_ACCESS: Final[TokenScope] = "access"
SCOPE_REGISTER: Final[TokenScope] = "register"

ISSUER: Final = "kavim"


class TokenError(Exception):
    """A JWT that is malformed, expired, wrongly signed, or wrongly scoped.

    One exception for every failure mode on purpose: the caller returns the same
    401 regardless, and distinguishing "expired" from "bad signature" in a
    response tells an attacker which of the two they achieved.
    """


def _encode(payload: dict[str, Any], ttl: timedelta) -> str:
    now = utc_now()
    claims: dict[str, Any] = {
        **payload,
        "iss": ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        # A unique id per token, so a specific one can be denylisted later
        # without invalidating every token for that user.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        claims,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def _decode(token: str, *, expected_scope: TokenScope) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            # A list, and `algorithms` is not optional: accepting the algorithm
            # named in the token's own header is the classic "alg: none" forgery.
            algorithms=[settings.JWT_ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "iss", "sub", "scope"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if claims.get("scope") != expected_scope:
        raise TokenError(f"expected scope {expected_scope}, got {claims.get('scope')!r}")
    return claims


def create_access_token(
    user_id: uuid.UUID,
    *,
    email: str,
    roles: list[str] | None = None,
) -> str:
    """Short-lived bearer token, held in memory by the client only (SPEC §8.2).

    Roles are embedded for cheap UI gating, but they are **not** the
    authorization decision — `require_permission` re-resolves from the database,
    because a role revoked 30 seconds ago must not still work.
    """
    return _encode(
        {"sub": str(user_id), "email": email, "roles": roles or [], "scope": SCOPE_ACCESS},
        timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
    )


def decode_access_token(token: str) -> dict[str, Any]:
    return _decode(token, expected_scope=SCOPE_ACCESS)


def create_registration_ticket(invitation_id: uuid.UUID, email: str) -> str:
    """Proof that this browser passed the OTP check, valid 15 minutes.

    Carrying the invitation id rather than the raw invitation token is what lets
    the flow stop resending that token: after OTP verification the raw token is
    never transmitted again (SPEC §8.1). The email is carried too so registration
    can bind the account to the invited address without trusting the form.
    """
    return _encode(
        {"sub": str(invitation_id), "email": email, "scope": SCOPE_REGISTER},
        timedelta(minutes=15),
    )


def decode_registration_ticket(token: str) -> tuple[uuid.UUID, str]:
    """Returns ``(invitation_id, email)``."""
    claims = _decode(token, expected_scope=SCOPE_REGISTER)
    try:
        invitation_id = uuid.UUID(str(claims["sub"]))
    except (ValueError, KeyError) as exc:
        raise TokenError("registration ticket carries no valid invitation id") from exc
    email = str(claims.get("email") or "")
    if not email:
        raise TokenError("registration ticket carries no email")
    return invitation_id, email


# ══════════════════════════════════════════════════════════════════════════
#  refresh tokens
# ══════════════════════════════════════════════════════════════════════════
def new_refresh_token() -> tuple[str, str]:
    """Returns ``(raw_token, token_hash)``.

    The raw value goes to the client in an httpOnly cookie and is never stored;
    only the digest is persisted, so a database dump yields no usable session.
    """
    raw = generate_token()
    return raw, hash_token(raw)


def refresh_expiry(now: datetime | None = None) -> datetime:
    return (now or utc_now()) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)


def new_token_family() -> uuid.UUID:
    """A fresh rotation chain. One family per login, shared by every rotation
    descended from it, so reuse detection can revoke the whole chain at once."""
    return uuid.uuid4()
