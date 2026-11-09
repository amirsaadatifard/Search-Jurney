"""The automation engine interface.

The session runner (state machine, timeline, dwell loop) is written against this
interface only. The Playwright implementation lives in ``playwright_engine``;
tests substitute a fake, so every runner rule is verified without a browser.

One journey reaches its target in one of two shapes:

* the search journey opens Google in the browser, types the keyword into the
  page's own box, walks the result pages and clicks the target's own link
  (:meth:`AutomationEngine.search`, :meth:`~AutomationEngine.next_results_page`,
  :meth:`~AutomationEngine.click_result`). Google's pages are the only ones a
  session may open that are not an approved target site, and only while
  :meth:`~AutomationEngine.open_search_scope` is in effect;
* a direct visit - a target Google can never list, such as the bundled loopback
  sample site - calls nothing but :meth:`~AutomationEngine.open_url`.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Any

from src.automation.google_search_page import MAX_RESULTS, PageResult
from src.automation.interaction_service import ScheduledAction
from src.automation.models import InteractionOutcome, SearchPageRead, SessionPlan
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
    async def open_search_scope(self) -> None:
        """Allow Google's own pages in this session until :meth:`close_search_scope`.

        The scope is what lets a journey browse a search engine at all: while it
        is open, the request guard admits the origins and paths of
        :class:`~src.services.safety_service.SearchScope` and nothing else. Every
        caller must close it again in a ``finally`` block, and closing blanks the
        tab when it still holds Google's page - so no screenshot, scope check or
        dwell ever has to know about Google.
        """

    @abstractmethod
    async def close_search_scope(self) -> None:
        """End the search scope, blanking the tab when it is still on Google."""

    @abstractmethod
    async def search(self, plan: SessionPlan, *, limit: int = MAX_RESULTS) -> SearchPageRead:
        """Type ``plan.keyword`` into Google's own box and return the first page.

        An implementation opens the search engine's start page, finds its search
        field, types the keyword and presses Enter - and falls back to opening the
        results URL when the field cannot be used, reporting which route it took.
        A page that is not a results page, and a page Google refuses to answer,
        must raise: never an empty read.
        """

    @abstractmethod
    async def next_results_page(
        self, plan: SessionPlan, read: SearchPageRead, *, limit: int = MAX_RESULTS
    ) -> SearchPageRead | None:
        """Walk to the next page of results, or return ``None`` if Google offers none.

        Clicking the page's own "next" control is the route; following its href is
        the fallback. A next page that arrives without results raises.
        """

    @abstractmethod
    async def click_result(self, result: PageResult, *, from_url: str = "") -> str:
        """Click the result that was read and return the URL the tab landed on.

        Implementations click the anchor the page produced for ``result`` - never a
        URL handed to them separately - and verify the landing page through
        ``DomainSafetyService.check_redirect``, so a result that escapes the
        approved scope fails the session instead of browsing to it.
        """

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
