"""The automation engine interface.

The session runner (state machine, timeline, dwell loop) is written against this
interface only. The Playwright implementation lives in ``playwright_engine``;
tests substitute a fake, so every runner rule is verified without a browser.

The engine opens *pages*; it never searches. Reading the real ranking of a site
is the job of a search provider
(:mod:`src.search_providers`), so no browser is ever driven against a search
engine.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Any

from src.automation.interaction_service import ScheduledAction
from src.automation.models import InteractionOutcome, SessionPlan
from src.domain.enums import ScreenshotKind


class AutomationEngine(ABC):
    """Operations a browser backend must provide.

    All methods raise :class:`~src.domain.exceptions.AutomationError`
    subclasses (or ``SafetyViolationError``) for expected failures.
    """

    @property
    @abstractmethod
    def current_url(self) -> str:
        """URL of the page the session is on ('' before the first navigation)."""

    @property
    def is_open(self) -> bool:
        """Whether a page is currently open (``False`` before the browser starts)."""
        return False

    @abstractmethod
    async def start_session(self, plan: SessionPlan) -> dict[str, Any]:
        """Launch the browser if needed, open an isolated context and return browser info."""

    @abstractmethod
    async def open_url(self, url: str) -> str:
        """Open ``url`` and return the final URL after redirects."""

    @abstractmethod
    async def perform_safe_interaction(
        self, action: ScheduledAction, rng: random.Random
    ) -> InteractionOutcome:
        """Run one scheduled interaction inside the approved site."""

    @abstractmethod
    async def capture_screenshot(
        self, kind: ScreenshotKind, session_id: int, keyword: str
    ) -> dict[str, Any] | None:
        """Save a screenshot; returns its metadata or ``None`` if nothing was captured."""

    @abstractmethod
    async def end_session(self) -> None:
        """Close the session's browser context (and clear state if the plan says so)."""

    async def capture_preview(self) -> bytes | None:
        """A small JPEG of the current page for the live view (``None`` if unavailable)."""
        return None

    def drain_blocked(self) -> list[dict[str, Any]]:
        """Requests the safety guard blocked since the last call (empty by default)."""
        return []

    @abstractmethod
    async def close(self) -> None:
        """Release the browser itself at the end of a run."""
