"""Time helpers (SPEC NFR-08).

Two rules, and every date bug in this class of application comes from breaking
one of them:

1. **Store UTC.** Every `TIMESTAMPTZ` is UTC. Never persist a naive datetime.
2. **Compute "today" in the *user's* timezone, not the server's.**
   `date.today()` reads the process timezone. On a server in UTC, at 01:00
   Jerusalem time, `date.today()` returns *yesterday* — so an overdue scan would
   mark a task overdue a day early, and a due-date picker would default to the
   wrong day. Ruff's DTZ rules ban the naive calls outright.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings


def utc_now() -> datetime:
    """Timezone-aware current time in UTC. Use this, never `datetime.now()`."""
    return datetime.now(UTC)


def app_timezone() -> ZoneInfo:
    return ZoneInfo(settings.DEFAULT_TIMEZONE)


def local_now(timezone_name: str | None = None) -> datetime:
    """Current time in the user's (or the application's default) timezone."""
    tz = ZoneInfo(timezone_name) if timezone_name else app_timezone()
    return datetime.now(tz)


def local_today(timezone_name: str | None = None) -> date:
    """Today's calendar date where the *user* is.

    This is what due dates, overdue checks, and digest scheduling must use.
    """
    return local_now(timezone_name).date()


def to_local(moment: datetime, timezone_name: str | None = None) -> datetime:
    """Render a stored UTC instant in a display timezone."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    tz = ZoneInfo(timezone_name) if timezone_name else app_timezone()
    return moment.astimezone(tz)


def start_of_local_day(day: date, timezone_name: str | None = None) -> datetime:
    """The UTC instant at which `day` begins in the given timezone.

    Needed for "everything due today" queries: the boundary is local midnight,
    which is not midnight UTC.
    """
    tz = ZoneInfo(timezone_name) if timezone_name else app_timezone()
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def end_of_local_day(day: date, timezone_name: str | None = None) -> datetime:
    """Exclusive upper bound — the start of the following local day."""
    return start_of_local_day(day + timedelta(days=1), timezone_name)


def is_within_quiet_hours(
    moment: datetime, start: time | None, end: time | None, timezone_name: str | None = None
) -> bool:
    """Whether `moment` falls inside a user's quiet-hours window (FR-706).

    Handles a window that wraps midnight (22:00 → 06:00), which is the common
    case and the one a naive `start <= t <= end` comparison gets wrong.
    """
    if start is None or end is None:
        return False

    local_time = to_local(moment, timezone_name).time()

    if start <= end:
        return start <= local_time < end
    return local_time >= start or local_time < end
