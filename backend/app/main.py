"""Kavim application factory.

Run locally:
    uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Response, status
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.config import settings
from app.core.database import check_database, dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import register_middleware
from app.core.redis import check_redis, close_redis
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ══════════════════════════════════════════════════════════════════════════
#  health
# ══════════════════════════════════════════════════════════════════════════
health_router = APIRouter(tags=["health"])


@health_router.get("/health/live", summary="Liveness probe")
async def health_live() -> dict[str, str]:
    """The process is up and serving. Deliberately checks nothing else —
    a dependency outage must not cause the orchestrator to kill the container.
    """
    return {"status": "alive", "version": __version__}


@health_router.get("/health/ready", summary="Readiness probe")
async def health_ready(response: Response) -> dict[str, Any]:
    """Dependencies are reachable, so this instance can take traffic.

    Returns 503 when a dependency is down, which is what makes a load balancer
    stop routing to it instead of serving errors.
    """
    db_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    ready = db_ok and redis_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not_ready",
        "version": __version__,
        "environment": settings.APP_ENV,
        "checks": {
            "database": "ok" if db_ok else "unreachable",
            "redis": "ok" if redis_ok else "unreachable",
        },
    }


# ══════════════════════════════════════════════════════════════════════════
#  lifespan
# ══════════════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger.info(
        "startup",
        app=settings.APP_NAME,
        version=__version__,
        environment=settings.APP_ENV,
        locale=settings.DEFAULT_LOCALE,
        timezone=settings.DEFAULT_TIMEZONE,
    )

    # Report dependency state at boot but do not refuse to start. A container
    # that exits because Postgres is 5 seconds behind is worse than one that
    # reports "not ready" until it catches up.
    db_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    if not db_ok:
        logger.warning("startup_database_unreachable", hint="is the db container running?")
    if not redis_ok:
        logger.warning("startup_redis_unreachable", hint="is the redis container running?")
    if db_ok and redis_ok:
        logger.info("startup_dependencies_ok")

    try:
        yield
    finally:
        logger.info("shutdown")
        await dispose_engine()
        await close_redis()


# ══════════════════════════════════════════════════════════════════════════
#  factory
# ══════════════════════════════════════════════════════════════════════════
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description="Production line quality review and task management.",
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are a development affordance, not a public endpoint.
        # Off in production unless `API_DOCS_ENABLED=true` says otherwise — and
        # even then the reverse proxy 404s these paths, so the only route to
        # them is an SSH tunnel to the loopback port (docs/DEPLOYMENT.md).
        # The schema itself stays at the root, because that is the path nginx
        # forwards after stripping the public prefix.
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        # The docs *pages* are registered by hand below, so their link to the
        # schema can carry the public prefix. FastAPI's built-ins cannot: they
        # emit `openapi_url` verbatim, which the browser resolves against the
        # origin — `https://host/openapi.json`, outside the app entirely.
        docs_url=None,
        redoc_url=None,
        # **Not `root_path`.** It looks like the answer and is the opposite of it:
        # `root_path` tells Starlette the prefix is present in incoming paths, so
        # with nginx already stripping `/kavim` every request 404s except the ones
        # that still carry it. That is exactly what happened on the first deploy
        # of this file — the SPA's HTML loaded and every asset came back 404.
    )

    register_middleware(app)
    register_exception_handlers(app)

    _register_docs(app)
    app.include_router(health_router)

    # ── /api/v1 ───────────────────────────────────────────────────────────
    # Feature routers mount here as each module lands.
    api = APIRouter(prefix=settings.API_PREFIX)
    api.include_router(auth_router)
    api.include_router(users_router)
    api.include_router(admin_router)

    @api.get("/", tags=["meta"], summary="API root")
    async def api_root() -> dict[str, Any]:
        return {
            "name": settings.APP_NAME,
            "version": __version__,
            "api_version": "v1",
            "locales": settings.SUPPORTED_LOCALES,
            "default_locale": settings.DEFAULT_LOCALE,
            "timezone": settings.DEFAULT_TIMEZONE,
        }

    app.include_router(api)

    _mount_spa(app)
    return app


def _register_docs(app: FastAPI) -> None:
    """Swagger and ReDoc, with a schema URL the browser can actually resolve.

    Hand-registered rather than left to `docs_url`/`redoc_url` for one reason:
    the built-in pages emit `openapi_url` exactly as given, and a root-relative
    `/openapi.json` is wrong the moment a reverse proxy serves the app under a
    prefix — the browser asks the *host* for it and gets whatever else lives
    there. Prefixing the link is all that is needed; the schema route itself
    stays at the root, because that is the path nginx forwards.

    At a host root `APP_PUBLIC_PATH` is empty and this produces exactly what
    FastAPI would have produced on its own.
    """
    if not settings.docs_enabled:
        return

    schema_url = f"{settings.APP_PUBLIC_PATH}/openapi.json"

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url=schema_url, title=f"{settings.APP_NAME} — API")

    @app.get("/redoc", include_in_schema=False)
    async def redoc_ui() -> HTMLResponse:
        return get_redoc_html(openapi_url=schema_url, title=f"{settings.APP_NAME} — API")

    logger.info("docs_mounted", schema_url=schema_url)


def _mount_spa(app: FastAPI) -> None:
    """Serve the built frontend from the same origin, when it is present.

    In development the SPA is served by Vite on :5173, so this is a no-op.
    In production the release pipeline copies ``frontend/dist`` into
    ``app/static`` and this makes it a single-origin deployment: one port, no
    CORS (SPEC §5.5).
    """
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        logger.debug("spa_not_mounted", reason="app/static/index.html not found")
        return

    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """Client-side routing fallback.

        Any unmatched path returns index.html so a deep link such as
        /projects/<id>/board works on a hard refresh. API and health routes are
        registered before this, so they still win.
        """
        candidate = (STATIC_DIR / full_path).resolve()
        # Containment check — never serve outside the static directory.
        if full_path and candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    logger.info("spa_mounted", directory=str(STATIC_DIR))


app = create_app()
