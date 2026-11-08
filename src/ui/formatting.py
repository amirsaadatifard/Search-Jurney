"""Display formatting shared by pages (timestamps are stored as naive UTC)."""

from __future__ import annotations

from datetime import datetime

from src.services.timeutil import to_local

PLACEHOLDER = "—"

__all__ = [
    "PLACEHOLDER",
    "format_datetime",
    "format_seconds",
    "format_time",
    "to_local",
    "yes_no",
]


def format_datetime(value: datetime | None) -> str:
    return PLACEHOLDER if value is None else to_local(value).strftime("%Y-%m-%d %H:%M")


def format_time(value: datetime | None) -> str:
    return PLACEHOLDER if value is None else to_local(value).strftime("%H:%M:%S")


def format_seconds(value: float | None) -> str:
    """Render a duration such as ``42 s`` or ``3 min 05 s``."""
    if value is None:
        return PLACEHOLDER
    total = round(value)
    if total < 60:
        return f"{total} s"
    minutes, seconds = divmod(total, 60)
    return f"{minutes} min {seconds:02d} s"


def yes_no(value: bool) -> str:
    return "Yes" if value else "No"
