"""Password hashing, token generation, and constant-time comparison.

Phase 1 covers the primitives the seed script and Phase 2 both need. JWT
encoding and the OTP/invitation service logic land in Phase 2 on top of these.

Design rules, all from SPEC §8.3:

* Passwords: **argon2id**. Not bcrypt (72-byte truncation, no memory hardness),
  not PBKDF2 (cheap to attack on GPUs).
* Tokens and OTP codes: only a SHA-256 digest is stored. A database dump yields
  no usable invitation link, reset link, or session token.
* Every secret comparison is constant time, so response timing leaks nothing.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

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
