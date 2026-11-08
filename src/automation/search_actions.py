"""Search-page actions: open, type the keyword, submit, read the results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from playwright.async_api import Error as PlaywrightError

from src.automation.browser_manager import BrowserSession, translate_error
from src.automation.models import ResultItem
from src.domain.exceptions import NavigationError, SearchError
from src.local_servers.search_simulator import selectors as sel
from src.services.safety_service import DomainSafetyService

if TYPE_CHECKING:
    from playwright.async_api import Page

TYPING_DELAY_MS: Final = 35
LONG_KEYWORD_LENGTH: Final = 60
FAST_TYPING_DELAY_MS: Final = 4

_PARSE_RESULTS_JS: Final = f"""
items => items.map(item => {{
  const link = item.querySelector('a.{sel.RESULT_LINK_CLASS}');
  const text = cls => (item.querySelector('.' + cls)?.textContent || '').trim();
  return {{
    position: Number(item.getAttribute('{sel.RESULT_POSITION_ATTRIBUTE}')),
    title: text('{sel.RESULT_TITLE_CLASS}'),
    url: link ? link.href : '',
    description: text('{sel.RESULT_DESCRIPTION_CLASS}'),
  }};
}})
"""


class SearchActions:
    def __init__(self, safety: DomainSafetyService) -> None:
        self._safety = safety

    async def goto(self, session: BrowserSession, url: str) -> str:
        """Navigate to ``url`` and verify that the page we landed on is still in scope."""
        page = session.page
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise translate_error(exc, session, f"opening {url}") from exc
        self._safety.check_redirect(url, page.url)
        return page.url

    async def open(self, session: BrowserSession, url: str) -> str:
        """Open the search page and verify that it has a search box."""
        await self.goto(session, url)
        page = session.page
        if not await page.query_selector(f"#{sel.SEARCH_INPUT_ID}"):
            raise SearchError(f"{url} does not look like the search page (no search box).")
        return page.url

    async def fill(self, session: BrowserSession, keyword: str) -> None:
        field = session.page.locator(f"#{sel.SEARCH_INPUT_ID}")
        delay = TYPING_DELAY_MS if len(keyword) <= LONG_KEYWORD_LENGTH else FAST_TYPING_DELAY_MS
        try:
            await field.click()
            await field.fill("")
            await field.press_sequentially(keyword, delay=delay)
        except PlaywrightError as exc:
            raise translate_error(exc, session, "entering the keyword") from exc

    async def submit(self, session: BrowserSession) -> None:
        page = session.page
        before = page.url
        try:
            async with page.expect_navigation(wait_until="domcontentloaded"):
                await page.click(f"#{sel.SEARCH_BUTTON_ID}")
            await page.wait_for_selector(
                f"#{sel.RESULTS_LIST_ID}, #{sel.NO_RESULTS_ID}", state="attached"
            )
        except PlaywrightError as exc:
            raise translate_error(exc, session, "submitting the search") from exc
        self._safety.check_redirect(before, page.url)

    async def read_results(self, session: BrowserSession) -> list[ResultItem]:
        page: Page = session.page
        try:
            rows = await page.eval_on_selector_all(
                f"#{sel.RESULTS_LIST_ID} li.{sel.RESULT_CLASS}", _PARSE_RESULTS_JS
            )
        except PlaywrightError as exc:
            raise translate_error(exc, session, "reading the results") from exc
        results = [
            ResultItem(int(row["position"]), row["title"], row["url"], row["description"])
            for row in rows
            if row["url"] and row["position"]
        ]
        if not results and not await page.query_selector(f"#{sel.NO_RESULTS_ID}"):
            raise NavigationError("The results page has no results list.")
        return results
