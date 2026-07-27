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

# Task names are spelled out as literals rather than imported from the task
# modules, and that is deliberate. Importing `tasks_notifications` here would
# make this module depend on `celery_app`, which now imports *this* module — a
# genuine cycle that resolves or explodes depending on which side Python enters
# from, which is the worst kind of working.
#
# The cost is that a renamed task would drift from its schedule silently, so
# `tests/unit/test_beat_schedule.py` asserts every name here against the constant
# the task actually registers under, and that every one of them is registered.
BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "outbox-sweep": {
        "task": "kavim.notifications.sweep_outbox",
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

# This module is **data only**, and that is the fix for a real defect. It used to
# assign itself onto the app here — but nothing imported it, so
# `celery -A app.workers.celery_app beat` loaded an app whose `beat_schedule` was
# `{}`. Beat started, reported healthy, and dispatched nothing, forever. Queued
# OTP mail simply never left.
#
# The assignment now happens in `celery_app.py`, which is the module beat
# actually loads. `tests/unit/test_beat_schedule.py` pins it.
