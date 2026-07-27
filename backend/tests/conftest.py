"""Shared test fixtures.

Integration tests run against a **real PostgreSQL** container, never SQLite.
JSONB, GIN, `CITEXT`, generated columns, partial indexes, and `SKIP LOCKED` all
behave differently or do not exist in SQLite, so testing against it would
validate the wrong database and pass while production breaks.

Isolation strategy: the container and schema are created once per session
(migrations are slow), then each test runs inside a transaction that is rolled
back afterwards. Tests therefore share a schema but never share data.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

# Set before importing the app so settings pick these up.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")

# Captured at conftest import — before any fixture has had a chance to patch
# them — so `redis_down` can put the *genuine* swallow-and-log helpers back and
# exercise their try/except for real. Reading them off the module inside the
# fixture would hand back whatever `permission_cache` already substituted.
from app.core import redis as _redis

_REAL_REDIS_HELPERS = {
    name: getattr(_redis, name)
    for name in ("cache_get_json", "cache_set_json", "cache_delete", "cache_delete_prefix")
}


# ══════════════════════════════════════════════════════════════════════════
#  unit-level fixtures (no database)
# ══════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_instance() -> object:
    from app.main import create_app

    return create_app()


@pytest.fixture
async def client(app_instance: object) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client against the ASGI app.

    ``ASGITransport`` does not run the lifespan, so no database or Redis
    connection is opened — these tests stay hermetic.
    """
    transport = ASGITransport(app=app_instance)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ══════════════════════════════════════════════════════════════════════════
#  integration fixtures (real PostgreSQL)
# ══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    """Start a throwaway PostgreSQL 16 container for the session.

    Uses the same image tag as docker-compose so tests and development cannot
    diverge on server version.
    """
    pytest.importorskip("testcontainers.postgres", reason="testcontainers not installed")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "postgres:16-alpine",
        username="kavim_test",
        password="kavim_test",
        dbname="kavim_test",
        driver="asyncpg",
    ) as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
async def engine(postgres_url: str) -> AsyncGenerator[AsyncEngine, None]:
    """Engine with the full schema applied via Alembic.

    Migrations rather than ``metadata.create_all``, deliberately: this is what
    makes the test suite prove the migrations are correct. ``create_all`` would
    happily build a schema the migrations cannot produce.
    """
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command
    from app.core.config import BACKEND_DIR

    test_engine = create_async_engine(postgres_url, poolclass=None)

    # The compose init script does not run for a testcontainer, so the
    # extensions the schema depends on are created here.
    async with test_engine.begin() as conn:
        for extension in ("pgcrypto", "citext", "pg_trgm", "btree_gin"):
            await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", postgres_url)

    # Alembic's env.py runs its own asyncio loop, so it must not be awaited from
    # inside this one — run it in a worker thread.
    import anyio

    await anyio.to_thread.run_sync(lambda: command.upgrade(alembic_cfg, "head"))

    yield test_engine
    await test_engine.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Session inside a transaction that is rolled back after the test.

    Every test sees the migrated schema and an empty-of-test-data database, with
    no per-test migration cost.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            # Matches `get_sessionmaker()`. Not cosmetic: with autoflush on, a
            # pending INSERT is silently visible to a later SELECT in the same
            # transaction, so code that forgets to flush passes under test and
            # returns stale data in production. That exact gap shipped an empty
            # roles list in the registration response.
            autoflush=False,
            # A `commit()` inside the code under test releases a savepoint
            # instead of the outer transaction, so the rollback below still
            # discards everything.
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def api(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client whose requests run inside the test's transaction.

    `get_db` is overridden to yield the *same* session the test holds, so a row
    the test creates is visible to the endpoint and a row the endpoint creates is
    visible to the test — and the whole thing is rolled back afterwards.

    The override **mirrors `get_db`'s commit-on-success, rollback-on-exception
    semantics**, and that is load-bearing rather than incidental. A fixture that
    simply yields the session without either would have hidden three real bugs:
    the failed-login counter, the account lock, and the refresh-reuse revocation
    are all written and then followed by a raise, so under the real dependency
    they were rolled back by the very error that recorded them. Tests can only
    catch that if they roll back too.

    This works inside the outer test transaction because the session joins it with
    ``join_transaction_mode="create_savepoint"``: a `commit()` releases a savepoint
    and opens a new one, so committed work survives a later `rollback()` exactly as
    it would in production, and the outer rollback still discards all of it.

    **Consequence for tests:** a rollback expires every ORM instance in the
    session, so an object the test created before an error-returning request is
    stale afterwards. Touching one of its attributes triggers a synchronous lazy
    load and raises `MissingGreenlet`. Hold plain values (an email string, a UUID)
    across request boundaries, or `await db.refresh(obj)` before reading again.
    """
    from app.core.database import get_db
    from app.main import create_app

    app = create_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def rate_limit_counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Point rate limiting at a per-test in-memory counter.

    Without this every test shares whatever Redis is on the machine, so a suite
    run twice inside 15 minutes starts failing on quotas — and the tests that
    deliberately exhaust a limit would leak into unrelated ones.

    Autouse *and* requestable: the returned dict is the live counter state, so a
    test that needs to isolate one control from another can clear it. Handy
    because SPEC §8.3 sets the login throttle and the lockout threshold to the
    same number, so the throttle answers first and hides the lock.
    """
    from app.core import rate_limit

    counters: dict[str, int] = {}

    async def _fake_consume(limit: rate_limit.Limit, identifier: str) -> rate_limit.LimitResult:
        key = f"{limit.name}:{identifier.lower()}"
        counters[key] = counters.get(key, 0) + 1
        used = counters[key]
        return rate_limit.LimitResult(
            allowed=used <= limit.max_events,
            used=used,
            limit=limit.max_events,
            retry_after_seconds=limit.window_seconds if used > limit.max_events else 0,
        )

    async def _fake_reset(limit: rate_limit.Limit, identifier: str) -> None:
        counters.pop(f"{limit.name}:{identifier.lower()}", None)

    monkeypatch.setattr(rate_limit, "consume", _fake_consume)
    monkeypatch.setattr(rate_limit, "reset", _fake_reset)
    return counters


@pytest.fixture(autouse=True)
def permission_cache(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Point the permission cache at a per-test in-memory dict.

    The same argument as `rate_limit_counters`. Without it, tests share whatever
    Redis is on the developer's machine: a permission set cached by one test
    leaks into the next, so the invalidation tests pass for the wrong reason —
    or the whole suite fails on a machine with no Redis at all.

    Returned live so a test can inspect the keys directly and assert that an
    invalidation *deleted* something, rather than inferring it from a status code
    that a stale cache could also produce.
    """
    from app.core import redis

    store: dict[str, object] = {}

    async def _get(key: str) -> object | None:
        return store.get(key)

    async def _set(key: str, value: object, ttl_seconds: int) -> None:
        store[key] = value

    async def _delete(*keys: str) -> None:
        for key in keys:
            store.pop(key, None)

    async def _delete_prefix(prefix: str) -> int:
        doomed = [key for key in store if key.startswith(prefix)]
        for key in doomed:
            del store[key]
        return len(doomed)

    monkeypatch.setattr(redis, "cache_get_json", _get)
    monkeypatch.setattr(redis, "cache_set_json", _set)
    monkeypatch.setattr(redis, "cache_delete", _delete)
    monkeypatch.setattr(redis, "cache_delete_prefix", _delete_prefix)
    return store


@pytest.fixture
def redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Redis unreachable, so the real swallow-and-log paths run.

    Two steps, and both are needed. The autouse `permission_cache` has already
    swapped the helpers for in-memory ones that cannot fail, so this puts the
    genuine implementations back and *then* breaks `get_redis` underneath them.
    Patching only `get_redis` would change nothing, because nothing would be
    calling it.

    Request it explicitly; it is not autouse.
    """
    from app.core import redis

    for name, real in _REAL_REDIS_HELPERS.items():
        monkeypatch.setattr(redis, name, real)

    def _explode() -> object:
        raise ConnectionError("redis is down (test fixture)")

    monkeypatch.setattr(redis, "get_redis", _explode)


@pytest.fixture
async def seeded_reference(db: AsyncSession) -> dict[str, object]:
    """Roles and permissions, as the real seed script creates them.

    Anything touching authorization needs these, and building them by hand in
    each test would drift from the seeder.
    """
    from app.scripts.seed import seed_permissions, seed_roles

    permissions = await seed_permissions(db)
    roles = await seed_roles(db, permissions)
    await db.flush()
    return {"permissions": permissions, "roles": roles}
