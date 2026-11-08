"""Locating the target in the results and opening it."""

from __future__ import annotations

from collections.abc import Sequence

from playwright.async_api import Error as PlaywrightError

from src.automation.browser_manager import BrowserSession, translate_error
from src.automation.models import ResultItem
from src.domain.exceptions import NavigationError, SafetyViolationError
from src.local_servers.search_simulator import selectors as sel
from src.services.safety_service import AllowedOrigin, DomainSafetyService, normalize_url


class TargetActions:
    def __init__(self, safety: DomainSafetyService) -> None:
        self._safety = safety

    @staticmethod
    def find_target(results: Sequence[ResultItem], target_url: str) -> ResultItem | None:
        """Return the first result on the target's host (and port, if one is set).

        Matching by origin rather than by exact URL means a result pointing at
        any page of the target site counts as the target.
        """
        target = AllowedOrigin.from_url(normalize_url(target_url))
        for item in sorted(results, key=lambda result: result.position):
            try:
                if target.matches(normalize_url(item.url)):
                    return item
            except SafetyViolationError:
                continue  # a malformed result URL is simply not the target
        return None

    async def open(self, session: BrowserSession, item: ResultItem) -> str:
        """Click the result link, like a user would, and verify where we landed."""
        page = session.page
        before = page.url
        selector = (
            f"li.{sel.RESULT_CLASS}[{sel.RESULT_POSITION_ATTRIBUTE}='{item.position}'] "
            f"a.{sel.RESULT_LINK_CLASS}"
        )
        try:
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await page.click(selector)
        except PlaywrightError as exc:
            raise translate_error(exc, session, f"opening result #{item.position}") from exc
        self._safety.check_redirect(before, page.url)
        if not page.url:
            raise NavigationError("The target page did not load.")
        return page.url
