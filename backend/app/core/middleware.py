"""HTTP middleware: request correlation, access logging, security headers."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Paths that must not fill the log with noise.
_QUIET_PATHS = frozenset({"/health/live", "/health/ready", "/metrics", "/favicon.ico"})

# Swagger UI and ReDoc load their bundle from jsdelivr and their favicon from
# fastapi.tiangolo.com, so the strict CSP blanks the page. These paths get the
# CDN allowance and nothing else does — the SPA must never depend on it.
#
# This applies in production too, when `API_DOCS_ENABLED` mounts the docs there.
# It used to say production never reaches here, which stopped being true the day
# the docs became optional rather than absent — and the symptom was a blank page
# behind a working password prompt.
_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})
_DOCS_CDN = "https://cdn.jsdelivr.net"
_DOCS_FAVICON_HOST = "https://fastapi.tiangolo.com"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log the request, and time it.

    An inbound ``X-Request-ID`` is honoured so a reverse proxy or the frontend
    can correlate its own logs with ours; otherwise one is generated. The value
    is echoed back and bound to a contextvar, which puts it on every log line
    for the request — including Celery tasks it enqueues (SPEC §12.3).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        # Never trust an unbounded client-supplied value into logs.
        request_id = incoming[:64] if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        finally:
            request_id_var.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)

        if request.url.path not in _QUIET_PATHS:
            log = logger.warning if response.status_code >= 500 else logger.info
            log(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
                request_id=request_id,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security headers (SPEC §8.3).

    The CSP is deliberately strict. In development the Vite dev server needs
    inline styles and a websocket for hot reload, so it is relaxed there only.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(self)"
        )

        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        # The docs branch is checked **before** the production one, and the order
        # is the whole point. Swagger loads its bundle from a CDN, so the strict
        # policy below blanks the page — which is exactly what happened the first
        # time the docs were enabled on a production host: the HTML arrived, the
        # JavaScript was refused, and the browser showed nothing at all.
        #
        # This widens the policy for three paths that only exist when
        # `API_DOCS_ENABLED` says so, and are additionally behind HTTP Basic auth
        # at the proxy. The SPA's policy is untouched and must never depend on a
        # third-party origin.
        if settings.docs_enabled and request.url.path in _DOCS_PATHS:
            csp = (
                "default-src 'self'; "
                f"img-src 'self' data: blob: {_DOCS_FAVICON_HOST}; "
                f"style-src 'self' 'unsafe-inline' {_DOCS_CDN}; "
                f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {_DOCS_CDN}; "
                "font-src 'self' data:; "
                "connect-src 'self'"
            )
        elif settings.is_production:
            csp = (
                "default-src 'self'; "
                "img-src 'self' data: blob:; "
                "style-src 'self'; "
                "script-src 'self'; "
                "font-src 'self'; "
                "connect-src 'self' wss:; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        elif request.url.path in _DOCS_PATHS:
            csp = (
                "default-src 'self'; "
                f"img-src 'self' data: blob: {_DOCS_FAVICON_HOST}; "
                f"style-src 'self' 'unsafe-inline' {_DOCS_CDN}; "
                f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {_DOCS_CDN}; "
                "font-src 'self' data:; "
                "connect-src 'self'"
            )
        else:
            csp = (
                "default-src 'self'; "
                "img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "font-src 'self' data:; "
                "connect-src 'self' ws: wss: http://localhost:* http://127.0.0.1:*"
            )
        response.headers.setdefault("Content-Security-Policy", csp)
        return response


def register_middleware(app: FastAPI) -> None:
    """Order matters — Starlette applies these outermost-first.

    Security headers wrap everything, then request context, then CORS.
    """
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # Production is single-origin (FastAPI serves the built SPA), so CORS is
    # only wired up when origins are explicitly configured.
    if settings.CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,  # required for the refresh-token cookie
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "If-Match",
                "Idempotency-Key",
                "Accept-Language",
                REQUEST_ID_HEADER,
            ],
            expose_headers=[
                "ETag",
                REQUEST_ID_HEADER,
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
                "Retry-After",
            ],
            max_age=3600,
        )
