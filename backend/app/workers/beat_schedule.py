"""Celery beat schedule (SPEC §6.13).

Beat MUST run as exactly one instance. Two schedulers double-send every
scheduled notification — enforced by deployment config, called out here and in
the release runbook.

The intended full schedule, for reference:

    every 10s   outbox sweep            claim pending rows, dispatch   ← live
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
        # Every 10 seconds, so the wait a user actually experiences averages ~5s
        # rather than ~15s. Lowered from 30s after the first production use: the
        # person watching the OTP screen is the one this interval is felt by, and
        # 30s there reads as "the mail never came".
        #
        # The cost is one claim query per tick — 8,640 a day against an indexed
        # `status`/`next_attempt_at`, which is nothing. It is not lowered further
        # because polling is the wrong tool below this: an OTP that must be
        # sub-second wants `send_task` right after the commit, with the sweep as
        # the floor rather than the only path (Phase 7).
        "schedule": timedelta(seconds=10),
        "options": {
            # If beat outruns the worker, drop the missed tick rather than queueing
            # a backlog of identical sweeps. Each sweep already picks up whatever is
            # pending, so a queued duplicate does no useful work and burns a slot.
            # Must stay below the interval, or an expired tick is impossible.
            "expires": 8,
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
