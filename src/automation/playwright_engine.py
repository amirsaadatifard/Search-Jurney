"""Playwright implementation of :class:`AutomationEngine`."""

from __future__ import annotations

import contextlib
import random
from collections.abc import Sequence
from typing import Any

from playwright.async_api import Error as PlaywrightError

from src.automation.browser_manager import BrowserManager, BrowserSession, translate_error
from src.automation.control import RunControl
from src.automation.engine import AutomationEngine
from src.automation.interaction_service import InteractionService, ScheduledAction
from src.automation.models import InteractionOutcome, ResultItem, SessionPlan
from src.automation.screenshot_service import ScreenshotService
from src.automation.search_actions import SearchActions
from src.automation.target_actions import TargetActions
from src.domain.enums import ScreenshotKind
from src.domain.exceptions import AutomationError, BrowserLaunchError
from src.services.safety_service import DomainSafetyService

PREVIEW_QUALITY = 55


class PlaywrightEngine(AutomationEngine):
    """Drives Chromium through small, single-purpose services."""

    def __init__(
        self,
        safety: DomainSafetyService,
        control: RunControl,
        screenshots: ScreenshotService,
    ) -> None:
        self._manager = BrowserManager(safety)
        self._search = SearchActions(safety)
        self._targets = TargetActions(safety)
        self._interactions = InteractionService(safety, control)
        self._screenshots = screenshots
        self._plan: SessionPlan | None = None
        self._session: BrowserSession | None = None
        self._reported = 0

    @property
    def manager(self) -> BrowserManager:
        return self._manager

    @property
    def current_url(self) -> str:
        if self._session is None:
            return ""
        try:
            return self._session.page.url
        except PlaywrightError:
            return ""

    def _active(self) -> BrowserSession:
        if self._session is None:
            raise BrowserLaunchError("No browser session is open.")
        return self._session

    async def start_session(self, plan: SessionPlan) -> dict[str, Any]:
        self._plan = plan
        self._reported = 0
        await self._manager.launch(plan.browser, plan)
        try:
            self._session = await self._manager.open_session(plan)
            return await self._manager.describe(self._session, plan.browser)
        except PlaywrightError as exc:
            raise BrowserLaunchError(f"The browser session could not be opened: {exc}") from exc

    async def navigate(self, url: str) -> str:
        return await self._search.open(self._active(), url)

    async def open_url(self, url: str) -> str:
        return await self._search.goto(self._active(), url)

    async def fill_search(self, keyword: str) -> None:
        await self._search.fill(self._active(), keyword)

    async def submit_search(self) -> None:
        await self._search.submit(self._active())

    async def get_results(self) -> list[ResultItem]:
        return await self._search.read_results(self._active())

    def find_target(self, results: Sequence[ResultItem], target_url: str) -> ResultItem | None:
        return self._targets.find_target(results, target_url)

    async def open_target(self, item: ResultItem) -> str:
        return await self._targets.open(self._active(), item)

    async def perform_safe_interaction(
        self, action: ScheduledAction, rng: random.Random
    ) -> InteractionOutcome:
        session = self._active()
        try:
            return await self._interactions.perform(session.page, action, rng)
        except PlaywrightError as exc:
            raise translate_error(exc, session, f"performing {action.kind.value}") from exc

    async def capture_screenshot(
        self, kind: ScreenshotKind, session_id: int, keyword: str
    ) -> dict[str, Any] | None:
        session = self._session
        if session is None:
            return None
        return await self._screenshots.capture(session.page, session_id, keyword, kind)

    async def capture_preview(self) -> bytes | None:
        session = self._session
        if session is None:
            return None
        try:
            return await session.page.screenshot(type="jpeg", quality=PREVIEW_QUALITY)
        except PlaywrightError:
            return None  # the page is navigating or closing; the next frame will do

    def drain_blocked(self) -> list[dict[str, Any]]:
        session = self._session
        if session is None:
            return []
        fresh = session.blocked[self._reported :]
        self._reported = len(session.blocked)
        return [
            {"url": item.url, "navigation": item.is_navigation, "reason": item.reason}
            for item in fresh
        ]

    async def end_session(self) -> None:
        session, plan = self._session, self._plan
        self._session = None
        if session is None or plan is None:
            return
        # The context may already be gone; there is nothing left to clean then.
        with contextlib.suppress(PlaywrightError, AutomationError):
            await self._manager.close_session(session, plan)

    async def close(self) -> None:
        await self._manager.close()
