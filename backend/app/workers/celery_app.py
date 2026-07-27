"""Celery application.

Run a worker:
    celery -A app.workers.celery_app.celery_app worker --loglevel=info
Run the scheduler (exactly one instance — two double-send every notification):
    celery -A app.workers.celery_app.celery_app beat --loglevel=info

Task modules are listed in ``include`` below. A task that is not listed is not
registered, and beat scheduling it produces `NotRegistered` at the first tick
rather than at import — so the list is the thing to check when a scheduled job
silently never runs.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, request_id_var

logger = get_logger(__name__)

celery_app = Celery(
    "kavim",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks_notifications"],
)

celery_app.conf.update(
    # JSON only — never pickle. Pickle deserialization from a broker is remote
    # code execution if the broker is ever compromised.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Acknowledge after completion so a killed worker's task is redelivered
    # rather than silently lost.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
)


@setup_logging.connect
def _configure_celery_logging(**_: Any) -> None:
    """Use the application's structlog configuration, not Celery's default."""
    configure_logging()


@task_prerun.connect
def _bind_request_id(task_id: str | None = None, **kwargs: Any) -> None:
    """Carry the originating request id into the task's log lines.

    A notification dispatched three minutes after a click stays traceable to
    that click (SPEC §12.3).
    """
    task_kwargs = kwargs.get("kwargs") or {}
    incoming = task_kwargs.get("request_id") if isinstance(task_kwargs, dict) else None
    request_id_var.set(incoming or (task_id or "celery"))


@task_postrun.connect
def _clear_request_id(**_: Any) -> None:
    request_id_var.set(None)


@celery_app.task(name="kavim.ping")
def ping() -> str:
    """Connectivity check. ``celery_app.send_task("kavim.ping")`` should return "pong"."""
    logger.info("celery_ping")
    return "pong"
