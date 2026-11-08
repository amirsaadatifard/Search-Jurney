"""Opening the target page of a journey."""

from __future__ import annotations

from playwright.async_api import Error as PlaywrightError

from src.automation.browser_manager import BrowserSession, translate_error
from src.domain.exceptions import NavigationError
from src.services.safety_service import DomainSafetyService


class TargetActions:
    """The only page-level action a journey performs: go to the approved URL."""

    def __init__(self, safety: DomainSafetyService) -> None:
        self._safety = safety

    async def open_url(self, session: BrowserSession, url: str) -> str:
        """Navigate to ``url`` and verify that the page we landed on is still in scope.

        The URL never comes from a results page the browser clicked on: it is the
        address the search provider reported for the approved site (or the site's
        own base URL). What still has to be checked is *where the browser ended
        up*, because a redirect may leave the approved origin.
        """
        page = session.page
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise translate_error(exc, session, f"opening {url}") from exc
        self._safety.check_redirect(url, page.url)
        if not page.url:
            raise NavigationError("The target page did not load.")
        return page.url
