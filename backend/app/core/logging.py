"""Structured logging.

JSON in production so logs are queryable; coloured key-value pairs in
development so they are readable. A ``request_id`` is bound to a contextvar by
the middleware and appears on every log line for that request — including lines
emitted by Celery tasks the request triggered (SPEC §12.3).

Two processor chains are configured, and they are not interchangeable:

* the **native** chain handles ``structlog`` loggers, which are backed by
  ``WriteLogger`` and have no stdlib ``LogRecord`` behind them;
* the **foreign** chain handles records from stdlib loggers (uvicorn,
  sqlalchemy, celery) and may therefore use the ``structlog.stdlib.*``
  processors that read attributes off a real ``logging.Logger``.

Mixing them raises ``AttributeError: 'WriteLogger' object has no attribute
'name'`` at the first log call, which is a startup failure, not a warning.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def _add_context(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Attach the ambient request/user ids to every event."""
    if (rid := request_id_var.get()) is not None:
        event_dict.setdefault("request_id", rid)
    if (uid := user_id_var.get()) is not None:
        event_dict.setdefault("user_id", uid)
    return event_dict


def _renderer() -> structlog.types.Processor:
    if settings.LOG_JSON:
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=True)


def configure_logging() -> None:
    """Configure structlog and route stdlib logging through it.

    Called once from the application factory and once from the Celery worker
    bootstrap, so the API and the workers produce identically shaped output.
    """
    renderer = _renderer()

    # ── native chain: structlog loggers (WriteLogger) ─────────────────────
    native_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*native_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.LOG_LEVEL]
        ),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # ── foreign chain: stdlib loggers (uvicorn, sqlalchemy, celery) ───────
    foreign_pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)

    for name, level in {
        "uvicorn": logging.INFO,
        "uvicorn.access": logging.WARNING,  # our middleware logs requests instead
        "uvicorn.error": logging.INFO,
        "sqlalchemy.engine": logging.WARNING,
        "celery": logging.INFO,
        "asyncio": logging.WARNING,
    }.items():
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level)


def get_logger(name: str | None = None) -> Any:
    """Module-level logger accessor.

    The name is bound as a regular event key rather than read off the logger,
    because the native chain has no stdlib logger to read it from.
    """
    logger = structlog.get_logger()
    return logger.bind(logger=name) if name else logger
