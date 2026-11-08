"""Time conversion helpers. The database stores naive UTC; users see local time."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta


def to_local(value: datetime) -> datetime:
    """Convert a naive UTC timestamp from the database to an aware local datetime."""
    return value.replace(tzinfo=UTC).astimezone()


def to_local_naive(value: datetime | None) -> datetime | None:
    """Local wall-clock time without a zone (what spreadsheets and CSV expect)."""
    return None if value is None else to_local(value).replace(tzinfo=None)


def local_day_start_utc(day: date) -> datetime:
    """Naive UTC instant at which local calendar ``day`` begins."""
    local_start = datetime.combine(day, time.min).astimezone()
    return local_start.astimezone(UTC).replace(tzinfo=None)


def local_day_range_utc(
    first: date | None, last: date | None
) -> tuple[datetime | None, datetime | None]:
    """Naive UTC ``[start, end)`` covering local days ``first`` through ``last`` inclusive."""
    start = None if first is None else local_day_start_utc(first)
    end = None if last is None else local_day_start_utc(last + timedelta(days=1))
    return start, end
