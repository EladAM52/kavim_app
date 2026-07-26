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
