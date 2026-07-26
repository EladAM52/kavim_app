"""Application errors and their HTTP rendering.

Every error the API returns is RFC 7807 ``application/problem+json`` (SPEC §9.1),
so the frontend has one shape to parse rather than three.

    {
      "type":     "https://kavim.app/errors/version-conflict",
      "title":    "Version conflict",
      "status":   409,
      "detail":   "The cell was changed by someone else.",
      "instance": "/api/v1/tasks/…/cells/status",
      "code":     "version_conflict",
      "errors":   [ {"field": "...", "message": "..."} ]
    }
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
ERROR_BASE_URI = "https://kavim.app/errors"


class AppError(Exception):
    """Base class for expected, client-visible failures.

    Anything raised that is *not* an ``AppError`` is treated as a bug: logged
    with a traceback and rendered as a generic 500 with no internal detail.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    title: str = "Internal error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.title
        self.errors = errors or []
        self.headers = headers or {}
        self.extra = extra or {}
        super().__init__(self.detail)

    def to_problem(self, instance: str) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"{ERROR_BASE_URI}/{self.code.replace('_', '-')}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
            "code": self.code,
        }
        if self.errors:
            problem["errors"] = self.errors
        if (rid := request_id_var.get()) is not None:
            problem["request_id"] = rid
        problem.update(self.extra)
        return problem


# ── 400 / 422 ─────────────────────────────────────────────────────────────
class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    title = "Bad request"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_failed"
    title = "Validation failed"


# ── 401 / 403 ─────────────────────────────────────────────────────────────
class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    title = "Authentication required"

    def __init__(self, detail: str | None = None, **kw: Any) -> None:
        super().__init__(detail, **kw)
        self.headers.setdefault("WWW-Authenticate", "Bearer")


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    title = "Permission denied"


class AccountLockedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "account_locked"
    title = "Account temporarily locked"


# ── 404 ───────────────────────────────────────────────────────────────────
class NotFoundError(AppError):
    """Also used for resources outside the caller's visibility.

    Returning 404 rather than 403 means the API never confirms the existence
    of a project the caller cannot see (SPEC §9.2).
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    title = "Not found"


# ── 409 / 410 ─────────────────────────────────────────────────────────────
class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    title = "Conflict"


class VersionConflictError(ConflictError):
    """Optimistic concurrency failure on a cell write (FR-504)."""

    code = "version_conflict"
    title = "Version conflict"

    def __init__(
        self,
        detail: str | None = None,
        *,
        current_version: int | None = None,
        current_value: Any = None,
        **kw: Any,
    ) -> None:
        extra: dict[str, Any] = kw.pop("extra", {})
        if current_version is not None:
            extra["current_version"] = current_version
        extra["current_value"] = current_value
        super().__init__(
            detail or "This item was changed by someone else since you loaded it.",
            extra=extra,
            **kw,
        )


class GoneError(AppError):
    """An invitation or token that expired or was already consumed (FR-102)."""

    status_code = status.HTTP_410_GONE
    code = "gone"
    title = "No longer available"


# ── 429 ───────────────────────────────────────────────────────────────────
class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    title = "Too many requests"

    def __init__(self, detail: str | None = None, *, retry_after: int = 60, **kw: Any) -> None:
        super().__init__(detail or "Too many requests. Try again shortly.", **kw)
        self.headers.setdefault("Retry-After", str(retry_after))


# ── 503 ───────────────────────────────────────────────────────────────────
class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    title = "Service temporarily unavailable"


class ProviderError(ServiceUnavailableError):
    """An external provider (SendGrid, Twilio, storage) failed."""

    code = "provider_error"
    title = "External provider unavailable"


# ── handlers ──────────────────────────────────────────────────────────────
def _problem_response(problem: dict[str, Any], headers: dict[str, str]) -> JSONResponse:
    return JSONResponse(
        status_code=int(problem["status"]),
        content=problem,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.error("app_error", code=exc.code, detail=exc.detail, path=request.url.path)
    else:
        logger.info("app_error", code=exc.code, detail=exc.detail, path=request.url.path)
    return _problem_response(exc.to_problem(request.url.path), exc.headers)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render Starlette/FastAPI HTTPExceptions in the same problem+json shape."""
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    problem = {
        "type": f"{ERROR_BASE_URI}/http-{exc.status_code}",
        "title": detail,
        "status": exc.status_code,
        "detail": detail,
        "instance": request.url.path,
        "code": f"http_{exc.status_code}",
    }
    if (rid := request_id_var.get()) is not None:
        problem["request_id"] = rid
    return _problem_response(problem, dict(exc.headers or {}))


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Flatten Pydantic validation errors into ``errors[]``."""
    assert isinstance(exc, RequestValidationError)
    errors = [
        {
            "field": ".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][0]),
            "message": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    problem = ValidationError("One or more fields are invalid.", errors=errors)
    return _problem_response(problem.to_problem(request.url.path), {})


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort. Log the traceback, tell the client nothing internal."""
    logger.exception("unhandled_exception", path=request.url.path, exc_type=type(exc).__name__)
    problem = AppError("An unexpected error occurred.")
    return _problem_response(problem.to_problem(request.url.path), {})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
