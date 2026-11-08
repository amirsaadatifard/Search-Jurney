"""Display formatting shared by pages (timestamps are stored as naive UTC)."""

from __future__ import annotations

from datetime import datetime
from typing import Final

from src.domain.enums import SearchProfile
from src.services.timeutil import to_local

PLACEHOLDER = "—"

# How a scenario finds its ranking, in the user's words rather than in enum names.
SEARCH_PROFILE_LABELS: Final = {
    SearchProfile.GOOGLE_BROWSER: "Google in the browser",
    SearchProfile.GOOGLE_API: "Google API",
}

__all__ = [
    "PLACEHOLDER",
    "SEARCH_PROFILE_LABELS",
    "format_datetime",
    "format_seconds",
    "format_time",
    "search_profile_label",
    "yes_no",
]


def search_profile_label(value: SearchProfile | str) -> str:
    """Label for a stored profile; anything unrecognised says so instead of lying."""
    try:
        profile = SearchProfile(value)
    except ValueError:
        return "Google API" if str(value) == "GOOGLE" else PLACEHOLDER
    return SEARCH_PROFILE_LABELS.get(profile, PLACEHOLDER)


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
