"""Declarative description of the sidebar navigation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PageKey(StrEnum):
    """Stable identifiers of the main sections."""

    DASHBOARD = "dashboard"
    SCENARIOS = "scenarios"
    KEYWORDS = "keywords"
    TARGETS = "targets"
    PROXIES = "proxies"
    BROWSER_PROFILES = "browser_profiles"
    GOOGLE_RESEARCH = "google_research"
    RUNNER = "runner"
    LIVE_VIEW = "live_view"
    SESSIONS = "sessions"
    LOGS = "logs"
    REPORTS = "reports"
    SETTINGS = "settings"
    ABOUT = "about"


@dataclass(frozen=True, slots=True)
class NavItem:
    key: PageKey
    title: str
    icon: str


@dataclass(frozen=True, slots=True)
class NavSection:
    title: str
    items: tuple[NavItem, ...]


NAVIGATION: tuple[NavSection, ...] = (
    NavSection("OVERVIEW", (NavItem(PageKey.DASHBOARD, "Dashboard", "dashboard"),)),
    NavSection(
        "CONFIGURATION",
        (
            NavItem(PageKey.SCENARIOS, "Scenarios", "scenarios"),
            NavItem(PageKey.KEYWORDS, "Keywords", "keywords"),
            NavItem(PageKey.TARGETS, "Target Sites", "targets"),
            NavItem(PageKey.PROXIES, "Proxy Profiles", "proxies"),
            NavItem(PageKey.BROWSER_PROFILES, "Browser Profiles", "browser"),
        ),
    ),
    NavSection(
        "EXECUTION",
        (
            NavItem(PageKey.RUNNER, "Automation Runner", "runner"),
            NavItem(PageKey.GOOGLE_RESEARCH, "Google Research", "search"),
            NavItem(PageKey.LIVE_VIEW, "Live Session View", "live"),
            NavItem(PageKey.SESSIONS, "Session History", "history"),
        ),
    ),
    NavSection(
        "INSIGHTS",
        (
            NavItem(PageKey.LOGS, "Logs", "logs"),
            NavItem(PageKey.REPORTS, "Reports", "reports"),
        ),
    ),
    NavSection(
        "SYSTEM",
        (
            NavItem(PageKey.SETTINGS, "Settings", "settings"),
            NavItem(PageKey.ABOUT, "About", "about"),
        ),
    ),
)


def all_nav_items() -> tuple[NavItem, ...]:
    """Flatten the navigation into display order."""
    return tuple(item for section in NAVIGATION for item in section.items)
