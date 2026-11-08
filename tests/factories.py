"""Builders for ORM objects with sensible defaults, to keep tests short."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.database.models import (
    AutomationSession,
    BrowserProfile,
    Keyword,
    ProxyProfile,
    Scenario,
    TargetSite,
)
from src.domain.enums import Environment, SessionState, SessionStatus


def make_target_site(**overrides: Any) -> TargetSite:
    values: dict[str, Any] = {
        "display_name": "Local Test Site",
        "base_url": "http://127.0.0.1:8766/",
        "domain": "127.0.0.1",
        "environment": Environment.LOCAL,
        "enabled": True,
        "approved_for_automation": True,
    }
    return TargetSite(**(values | overrides))


def make_browser_profile(**overrides: Any) -> BrowserProfile:
    return BrowserProfile(**({"name": "Default Desktop"} | overrides))


def make_proxy_profile(**overrides: Any) -> ProxyProfile:
    values: dict[str, Any] = {"name": "Lab Proxy", "host": "127.0.0.1", "port": 3128}
    return ProxyProfile(**(values | overrides))


def make_keyword(text: str = "gold ring", **overrides: Any) -> Keyword:
    return Keyword(**({"text": text, "tags": []} | overrides))


def make_scenario(
    target_site: TargetSite, browser_profile: BrowserProfile, **overrides: Any
) -> Scenario:
    values: dict[str, Any] = {
        "name": "Demo Scenario",
        "keyword": "gold ring",
        "target_site": target_site,
        "browser_profile": browser_profile,
        "min_dwell_seconds": 5,
        "max_dwell_seconds": 10,
        "iterations": 1,
    }
    return Scenario(**(values | overrides))


def make_session(
    started_at: datetime | None = None,
    status: SessionStatus = SessionStatus.COMPLETED,
    duration: float = 30.0,
    **overrides: Any,
) -> AutomationSession:
    """Build a finished session; ``finished_at`` is derived from ``duration``."""
    start = started_at or datetime(2026, 1, 15, 10, 0, 0)
    values: dict[str, Any] = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "scenario_name": "Demo Scenario",
        "keyword": "gold ring",
        "target_domain": "127.0.0.1",
        "target_url": "http://127.0.0.1:8766/",
        "status": status,
        "state": SessionState.COMPLETED,
        "started_at": start,
        "finished_at": start + timedelta(seconds=duration),
        "duration_seconds": duration,
        "target_found": status is SessionStatus.COMPLETED,
        "target_position": 3 if status is SessionStatus.COMPLETED else None,
        "actual_dwell_seconds": 12.0 if status is SessionStatus.COMPLETED else None,
    }
    return AutomationSession(**(values | overrides))
