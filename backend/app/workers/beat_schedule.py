"""Celery beat schedule (SPEC §6.13).

Beat MUST run as exactly one instance. Two schedulers double-send every
scheduled notification — enforced by deployment config, called out here and in
the release runbook.

The intended full schedule, for reference:

    every 30s   outbox sweep            claim pending rows, dispatch   ← live
    hourly      overdue scan            mark overdue, escalate once per 24h
    daily 07:00 digest builder          Asia/Jerusalem
    daily 03:00 token cleanup           expired OTP, invitations, refresh tokens
    daily 03:30 audit retention
    weekly      fractional-index rebalance
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.tasks_notifications import SWEEP_TASK_NAME

BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "outbox-sweep": {
        "task": SWEEP_TASK_NAME,
        # 30 seconds is the latency budget SPEC §6.8 accepts for a notification.
        # It is deliberately not shorter: an OTP a user is waiting for should not
        # be gated on a poll interval, so `send_task` may also be called directly
        # once there is a reason to — the sweep is the floor, not the only path.
        "schedule": timedelta(seconds=30),
        "options": {
            # If beat outruns the worker, drop the missed tick rather than queueing
            # a backlog of identical sweeps. Each sweep already picks up whatever is
            # pending, so a queued duplicate does no useful work and burns a slot.
            "expires": 25,
        },
    },
}

celery_app.conf.beat_schedule = BEAT_SCHEDULE
