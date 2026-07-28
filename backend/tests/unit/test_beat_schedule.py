"""The Celery wiring, asserted on the object beat actually loads.

`celery -A app.workers.celery_app beat` imports that module and reads everything
it will ever know off the `Celery()` instance inside it. So these tests import
the same module and check the same object — anything that is true here is true
for beat, and anything missing here is missing for beat.

**Why this file exists.** The schedule was declared in `beat_schedule.py` and that
module was never imported by anything, so beat read `conf.beat_schedule == {}`.
It started, logged "beat: Starting...", and dispatched nothing for as long as it
ran. Queued OTP mail never left the outbox. Nothing failed, because an empty
schedule is a legal schedule and a worker with no messages is a healthy worker —
the only symptom was a person who never received an email.

Session 6 tested `sweep()` directly and via the `--sweep` script. Both bypass
Redis, beat, and the worker, so both passed. Testing the function is not testing
the schedule.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.workers.beat_schedule import BEAT_SCHEDULE
from app.workers.celery_app import celery_app
from app.workers.tasks_notifications import SWEEP_TASK_NAME


def test_the_app_carries_a_non_empty_schedule() -> None:
    """The regression itself, in one line.

    Everything else in this file is refinement; this is the assertion that would
    have caught the defect.
    """
    assert celery_app.conf.beat_schedule, (
        "beat_schedule is empty — beat will start cleanly and dispatch nothing. "
        "Check that celery_app.py imports BEAT_SCHEDULE and assigns it."
    )


def test_the_installed_schedule_is_the_declared_one() -> None:
    """Guards the seam between the two files.

    `beat_schedule.py` declares; `celery_app.py` installs. A second assignment
    somewhere else, or a stale copy, shows up here.
    """
    assert celery_app.conf.beat_schedule == BEAT_SCHEDULE


@pytest.mark.parametrize("entry_name", list(BEAT_SCHEDULE))
def test_every_scheduled_task_is_registered(entry_name: str) -> None:
    """The failure mode `celery_app.py`'s docstring warns about, now checked.

    A task named in the schedule but absent from `include=` raises
    `NotRegistered` at the *first tick* — in the worker's log, in production, at
    whatever hour beat first fires it. Not at import, and not in CI.
    """
    task_name = BEAT_SCHEDULE[entry_name]["task"]
    assert task_name in celery_app.tasks, (
        f"{entry_name} schedules {task_name!r}, which is not registered. "
        f"Add its module to `include=` in celery_app.py."
    )


def test_the_scheduled_name_matches_the_name_the_task_registers_under() -> None:
    """The price of spelling task names as literals in `beat_schedule.py`.

    They are literals to keep that module free of any import of `celery_app`,
    which imports it back. The trade is that a renamed task would drift from its
    schedule without a word — beat would happily dispatch a name nothing answers
    to. This is the check that makes the literal safe.
    """
    assert BEAT_SCHEDULE["outbox-sweep"]["task"] == SWEEP_TASK_NAME


def test_the_outbox_sweep_runs_every_ten_seconds() -> None:
    """SPEC §6.8's notification latency budget, pinned.

    Not arbitrary: this is the delay a user waiting on an OTP experiences, and
    they average half of it. Lowered from 30s after the first production use,
    where 30s read as "the mail never came". Changing it is a product decision,
    so it should require editing a test rather than only a constant.
    """
    assert BEAT_SCHEDULE["outbox-sweep"]["schedule"] == timedelta(seconds=10)


def test_a_missed_sweep_tick_expires_rather_than_queueing() -> None:
    """`expires` must be shorter than the interval.

    If beat outruns the worker, an unexpired duplicate sits in the queue behind
    the running sweep and does no useful work when it gets there — the sweep it
    is duplicating has already claimed those rows. Worse, a backlog of them
    delays every genuinely new tick.
    """
    entry = BEAT_SCHEDULE["outbox-sweep"]
    expires = entry["options"]["expires"]
    assert expires < entry["schedule"].total_seconds()


def test_the_broker_never_accepts_pickle() -> None:
    """Deserializing pickle from a broker is remote code execution.

    Celery's historical default was pickle. This asserts the app has not drifted
    back to it — the kind of setting that gets loosened during a debugging
    session and never tightened again.
    """
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert list(celery_app.conf.accept_content) == ["json"]


def test_tasks_are_acknowledged_late() -> None:
    """A worker killed mid-sweep must have its task redelivered, not dropped."""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
