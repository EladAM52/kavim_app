"""Shared test fixtures.

Integration fixtures (a real PostgreSQL via testcontainers) arrive in Phase 1
alongside the models. Phase 0 covers the app surface that needs no database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Set before importing the app so settings pick these up.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")


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
