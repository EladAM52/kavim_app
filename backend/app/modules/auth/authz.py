"""Effective-permission resolution, cached in Redis (SPEC §8.4).

This is the read side of authorization. `core/permissions.py` holds the rules;
this module answers *what may this user actually do*, layer 1 (global role) ∩
layer 2 (project membership), and remembers the answer for five minutes.

It lives in `modules/auth/` rather than `core/` because it needs a database
session and the ORM, and the `core-independence` contract forbids that. SPEC §6.1
places it in `core/permissions.py`; the contract is the stronger constraint.

Why the cache fails soft — and why that is *not* the argument `rate_limit.py` makes
─────────────────────────────────────────────────────────────────────────────────
The two modules look alike and reach opposite conclusions, so the reasoning is
worth writing down before somebody harmonises them.

`rate_limit.py` fails **open**: when Redis is unreachable the control is skipped
entirely, and that is defensible only because a second control (a database
counter) sits behind every limit.

Nothing is skipped here. This is a read-through cache over a query against the
source of truth. When `cache_get_json` returns `None` — key absent, Redis down,
value corrupt, it makes no difference — the code takes the same branch it always
takes on a miss: query `role_permissions` in PostgreSQL and use that answer. A
Redis outage makes authorization *slower and strictly more current*. There is
nothing to fail open **to**.

Failing closed would mean returning an empty set on a Redis error, which converts
a Redis restart into every user receiving 403 on every route. That is a
self-inflicted outage bought for no security gain at all.

The residual risk is entirely on the **write** side: an invalidation that silently
fails leaves a revoked permission live for up to `CACHE_TTL_SECONDS`. Three things
bound it:

1. `cache_delete_prefix` logs `cache_prefix_delete_failed` — the failure is visible.
2. The revocations that matter most do not depend on this cache at all.
   `get_current_user` re-reads `users.is_active` from PostgreSQL on every request,
   so a deactivated user is locked out immediately; force-logout revokes refresh
   tokens in PostgreSQL. Only a *role downgrade* is bounded by the TTL.
3. 300s is shorter than the 900s access-token lifetime the system already accepts
   as its authorization staleness window, so the cache is not the weakest link.

The TTL is therefore not decoration even if every invalidation call site were
perfect: it is the backstop for a delete that fails while Redis is only partly
available.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis
from app.core.enums import ProjectPermissionLevel
from app.core.logging import get_logger
from app.core.permissions import resolve_effective_permissions
from app.models.project import ProjectMember
from app.modules.auth import service

logger = get_logger(__name__)

# `kavim:` keeps this separable from `kavim:rl:`, and the trailing user id makes
# `invalidate_user` a narrow prefix scan rather than a scan of the whole cache.
CACHE_PREFIX: Final = "kavim:perm:"

# Fixed by SPEC §8.4 at five minutes. Deliberately a constant rather than a
# setting: a new environment variable costs three synchronised edits (config,
# .env.example, the SPEC §12.1 table) for a number nobody should tune per
# environment, and a shorter value in one environment would hide cache bugs in
# the others.
CACHE_TTL_SECONDS: Final = 300


def cache_key(user_id: uuid.UUID, project_id: uuid.UUID | None) -> str:
    return f"{CACHE_PREFIX}{user_id}:{project_id or 'global'}"


async def project_level(
    db: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID
) -> ProjectPermissionLevel | None:
    """Layer 2. ``None`` means "not a member", which grants nothing."""
    level: ProjectPermissionLevel | None = await db.scalar(
        select(ProjectMember.permission_level).where(
            ProjectMember.user_id == user_id,
            ProjectMember.project_id == project_id,
        )
    )
    return level


async def effective_permissions(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    project_id: uuid.UUID | None = None,
    use_cache: bool = True,
) -> frozenset[str]:
    """What this user may do, globally or within one project.

    ``use_cache=False`` is not a performance switch, it is a correctness one, and
    it has two distinct callers:

    * **Inside an uncommitted transaction.** `build_identity` runs during
      `register` and `login`, before the commit. Caching a permission set that a
      later rollback erases would persist permissions for a user who does not
      exist.
    * **The FR-210 admin trace.** An administrator asking "why can this person
      edit this?" must be shown the live database, not a value up to five minutes
      stale — the whole point of the screen is to diagnose a discrepancy.
    """
    key = cache_key(user_id, project_id)

    if use_cache:
        cached = await redis.cache_get_json(key)
        if isinstance(cached, list):
            return frozenset(str(item) for item in cached)

    global_permissions = await service.load_global_permissions(db, user_id)

    if project_id is None:
        resolved = global_permissions
    else:
        resolved = resolve_effective_permissions(
            global_permissions, await project_level(db, user_id, project_id)
        )

    if use_cache:
        await redis.cache_set_json(key, sorted(resolved), CACHE_TTL_SECONDS)

    return resolved


# ── invalidation ──────────────────────────────────────────────────────────
# Call these *after* the commit, never before. Invalidating first is a live race:
# a concurrent request misses the cache, resolves from the pre-commit state it can
# still see, and repopulates the key with the stale value — which then survives
# the full TTL. Invalidating after narrows the window to the microseconds between
# the two calls, and the TTL bounds even that.
async def invalidate_user(user_id: uuid.UUID) -> None:
    """Drop every cached entry for one user, across all projects."""
    await redis.cache_delete_prefix(f"{CACHE_PREFIX}{user_id}:")


async def invalidate_all() -> None:
    """Drop the whole permission cache. Used after a role → permission edit.

    Enumerating the role's holders and invalidating each would be cheaper, and it
    would also be **wrong**: if the same transaction that changed the matrix also
    changed a role assignment, the membership list is read either before or after
    that change and misses somebody either way.

    A full prefix scan is affordable at this scale — under 50 users times a
    handful of projects is a few hundred keys — and it runs only on an explicit
    administrative action, never on a request path. Revisit if the user count
    reaches four digits, in the spirit of SPEC R8.
    """
    deleted = await redis.cache_delete_prefix(CACHE_PREFIX)
    logger.info("permission_cache_flushed", keys_deleted=deleted)


# Phase 4: project membership writes must call `invalidate_user(member_id)` —
# adding, changing, or removing a member changes layer 2 for exactly that user.
# Layer 3 (`board_columns.editable_by_roles`) is deliberately not cached: it is
# per-column, and it arrives free on the column row that any write already loads.
