"""Alembic environment.

The database URL comes from ``app.core.config`` rather than ``alembic.ini`` so
there is one source of truth and no risk of migrating the wrong database because
two files disagree.

Runs against the async engine, since that is the driver the application uses —
testing migrations on a different driver would validate the wrong thing.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings

# Importing the package registers every model on Base.metadata. Autogenerate
# only sees what is registered, so a model missing from app/models/__init__.py
# would be silently dropped.
from app.models import Base

config = context.config

# Honour a URL the caller already set — the integration-test fixture points
# Alembic at a throwaway container. Overwriting it unconditionally would migrate
# the *development* database instead, and the tests would then run against an
# empty schema.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.DATABASE_URL


if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(
    obj: Any, name: str | None, type_: str, _reflected: bool, _compare_to: Any
) -> bool:
    """Keep autogenerate focused on tables this project owns.

    PostgreSQL extensions create their own objects (pg_trgm, citext); without
    this filter, autogenerate would propose dropping them.
    """
    return not (type_ == "table" and name in {"spatial_ref_sys"})


def _configure(connection: Connection | None = None, **extra: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect column type and default changes, not just added/removed
        # columns — otherwise a widened VARCHAR silently never migrates.
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        # Render constraint names from the naming convention so downgrade knows
        # what to drop.
        render_as_batch=False,
        transaction_per_migration=True,
        **extra,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting — for review or manual application."""
    _configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
