"""Celery beat schedule (SPEC §6.13).

Beat MUST run as exactly one instance. Two schedulers double-send every
scheduled notification — enforced by deployment config, called out here and in
the release runbook.

Phase 0 registers nothing; entries are added as their tasks land in Phase 7.
The intended schedule, for reference:

    every 30s   outbox sweep            claim pending rows, dispatch
    hourly      overdue scan            mark overdue, escalate once per 24h
    daily 07:00 digest builder          Asia/Jerusalem
    daily 03:00 token cleanup           expired OTP, invitations, refresh tokens
    daily 03:30 audit retention
    weekly      fractional-index rebalance
"""

from __future__ import annotations

from typing import Any

from app.workers.celery_app import celery_app

BEAT_SCHEDULE: dict[str, dict[str, Any]] = {}

celery_app.conf.beat_schedule = BEAT_SCHEDULE
