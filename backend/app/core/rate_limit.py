"""Redis sliding-window rate limiting (SPEC §8.3).

**Why this fails open.** If Redis is unreachable, requests are allowed and a
warning is logged. That sounds wrong for a brute-force control, so it needs
justifying: every limit here sits in front of a second counter held in
PostgreSQL.

| Limit | Database backstop |
|---|---|
| login attempts | `users.failed_login_count` → 15-minute lock at 10 (FR-109) |
| OTP verify | `otp_codes.attempts` vs `max_attempts`, enforced in the same transaction |
| OTP request | one live invitation per email, and each request supersedes the last code |
| password reset | single-use token, 1-hour expiry |

So Redis is the cheap first line that keeps load off the database, and the
database holds the actual guarantee. Failing closed instead would convert a
Redis restart into a total login outage — a self-inflicted denial of service in
exchange for protection that is already there.

A sliding window, not a fixed one. A fixed window lets an attacker send the full
quota at 14:59 and again at 15:00, doubling the effective rate at every boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Final, cast

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.time import utc_now

logger = get_logger(__name__)

KEY_PREFIX: Final = "kavim:rl:"

# Check-and-consume has to be atomic: two concurrent requests that each read
# "9 used" would both proceed and land on 11. A pipeline does not help, because
# the decision happens between the read and the write. Lua runs server-side, so
# the whole thing is one indivisible step.
_SLIDING_WINDOW_LUA: Final = """
local key    = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window)
local used = redis.call('ZCARD', key)

if used >= limit then
  -- Report when the oldest entry falls out of the window, so Retry-After is a
  -- real number rather than a guess.
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window - (now_ms - tonumber(oldest[2]))
  return {0, used, retry_ms}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window)
return {1, used + 1, 0}
"""


@dataclass(frozen=True, slots=True)
class Limit:
    """A named quota. Defined once here rather than at call sites, so the numbers
    in SPEC §8.3 have exactly one home."""

    name: str
    max_events: int
    window_seconds: int

    @property
    def window_ms(self) -> int:
        return self.window_seconds * 1000


# SPEC §8.3. Login is limited per IP *and* per email: per-IP alone lets a
# botnet spread attempts across addresses, per-email alone lets one host walk a
# user list.
LOGIN_PER_IP: Final = Limit("login_ip", 10, 15 * 60)
LOGIN_PER_EMAIL: Final = Limit("login_email", 10, 15 * 60)
OTP_VERIFY: Final = Limit("otp_verify", settings.OTP_MAX_ATTEMPTS, 15 * 60)
OTP_REQUEST: Final = Limit("otp_request", settings.OTP_REQUEST_LIMIT_PER_15MIN, 15 * 60)
PASSWORD_RESET_REQUEST: Final = Limit("password_reset", 3, 15 * 60)


@dataclass(frozen=True, slots=True)
class LimitResult:
    allowed: bool
    used: int
    limit: int
    retry_after_seconds: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def headers(self) -> dict[str, str]:
        """`X-RateLimit-*` per SPEC §9.1, so a client can back off before being
        told to."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.retry_after_seconds),
        }


def _key(limit: Limit, identifier: str) -> str:
    return f"{KEY_PREFIX}{limit.name}:{identifier.lower()}"


async def consume(limit: Limit, identifier: str) -> LimitResult:
    """Record one event against ``identifier`` and report whether it is allowed.

    Never raises on a Redis failure — see the module docstring.
    """
    now = utc_now()
    now_ms = int(now.timestamp() * 1000)
    # Microsecond suffix keeps two events in the same millisecond from colliding
    # on the same sorted-set member, which would silently undercount.
    member = f"{now_ms}-{now.microsecond}"

    try:
        # redis-py types `eval` as the union of sync and async returns, so the
        # cast is about its stubs, not about what this call does.
        raw = await cast(
            "Awaitable[list[int]]",
            get_redis().eval(
                _SLIDING_WINDOW_LUA,
                1,
                _key(limit, identifier),
                str(now_ms),
                str(limit.window_ms),
                str(limit.max_events),
                member,
            ),
        )
    except Exception as exc:
        logger.warning(
            "rate_limit_unavailable",
            limit=limit.name,
            error=str(exc),
            note="allowing request; the database-side counter still applies",
        )
        return LimitResult(True, 0, limit.max_events, 0)

    allowed, used, retry_ms = (int(raw[0]), int(raw[1]), int(raw[2]))
    return LimitResult(
        allowed=bool(allowed),
        used=used,
        limit=limit.max_events,
        # Round up: a Retry-After of 0 invites an immediate retry that will also
        # be refused.
        retry_after_seconds=max(1, -(-retry_ms // 1000)) if retry_ms else 0,
    )


async def enforce(limit: Limit, identifier: str) -> LimitResult:
    """``consume`` but raises ``RateLimitError`` when the quota is spent."""
    result = await consume(limit, identifier)
    if not result.allowed:
        logger.info("rate_limited", limit=limit.name, retry_after=result.retry_after_seconds)
        raise RateLimitError(
            retry_after=result.retry_after_seconds,
            headers=result.headers(),
        )
    return result


async def reset(limit: Limit, identifier: str) -> None:
    """Clear a counter.

    Called after a *successful* login so one fumbled password does not eat into
    the quota of someone who then typed it correctly.
    """
    try:
        await get_redis().delete(_key(limit, identifier))
    except Exception as exc:
        logger.warning("rate_limit_reset_failed", limit=limit.name, error=str(exc))
