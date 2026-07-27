"""The outbox sweeper (SPEC §6.8, §6.13).

Beat fires `kavim.notifications.sweep_outbox` every 30 seconds. It claims a batch
with `SKIP LOCKED`, renders each message, hands it to the `EmailSender`, and
records the outcome — see `modules/notifications/outbox.py` for that logic. This
file is the Celery boundary and nothing more.

**Why `asyncio.run` per task rather than a shared loop.** Celery tasks are
synchronous, the database layer is async. Each task therefore owns a loop for its
own duration and disposes the engine at the end, because asyncpg connections
belong to the loop that created them — a pooled connection carried into the next
task's loop fails with `'NoneType' object has no attribute 'send'`. That exact
mistake already cost a debugging session in the seed script (see
`docs/PROGRESS.md`, session 2), so the disposal is deliberate rather than tidy.

The cost is a fresh connection per sweep. At one sweep per 30 seconds that is
irrelevant, and it buys immunity to a whole class of cross-loop bug.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.core.database import dispose_engine, get_sessionmaker
from app.core.logging import get_logger
from app.integrations.smtp_client import get_email_sender
from app.modules.notifications import outbox
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

SWEEP_TASK_NAME = "kavim.notifications.sweep_outbox"


async def _sweep_once(limit: int) -> outbox.SweepResult:
    """One claim-and-dispatch cycle in its own transaction.

    The commit covers the claim *and* every outcome, so a worker killed mid-batch
    leaves the rows rolled back to `pending` rather than half-processed. That is the
    property that makes redelivery safe.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            result = await outbox.sweep(session, sender=get_email_sender(), limit=limit)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return result


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async body and dispose the engine inside the same loop.

    Disposal must happen *before* the loop closes. Doing it in a second
    `asyncio.run` is the bug referenced in the module docstring.
    """

    async def _wrapper() -> T:
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(_wrapper())


@celery_app.task(
    name=SWEEP_TASK_NAME,
    # The next beat tick is 30s away, so a sweep that has not finished in 120s is
    # stuck; letting it run would stack overlapping sweeps.
    time_limit=120,
    soft_time_limit=100,
    # No autoretry. A failed sweep needs no retry — the rows it did not process are
    # still pending and the next tick picks them up. Retrying would double-send the
    # ones it did process.
    max_retries=0,
)
def sweep_outbox(limit: int = outbox.DEFAULT_BATCH_SIZE) -> dict[str, int]:
    """Claim and dispatch one batch of queued notifications."""
    try:
        result = _run(_sweep_once(limit))
    except Exception:
        # Logged with a traceback here because Celery's own error output does not
        # go through structlog, so it would otherwise miss the request-id binding.
        logger.exception("outbox_sweep_failed")
        raise

    if result.claimed:
        logger.info("outbox_sweep", **result.as_log_fields())
    return result.as_log_fields()


@celery_app.task(name="kavim.notifications.queue_depth")
def queue_depth() -> int:
    """Number of rows waiting or awaiting retry.

    Separate from the sweep so it can be polled for monitoring without triggering
    a dispatch.
    """

    async def _count() -> int:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            return await outbox.pending_count(session)

    depth: int = _run(_count())
    logger.info("outbox_queue_depth", depth=depth)
    return depth
