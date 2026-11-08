"""The one place the browser touches Google.

:class:`SearchPageActions` opens a Google results page, reads its result links and
leaves the tab on a blank page again. It is deliberately incapable of anything
else: it does not click a result, does not scroll, does not re-query, does not
retry a refusal and does not try to get past a captcha. The consent screen is the
single button it will press, because leaving a European consent dialog unanswered
would mean no page at all.

Everything that decides *what* to ask for, and what the page means, lives in
:mod:`src.automation.google_search_page`; this class only speaks to Playwright.
"""

from __future__ import annotations

import contextlib
from typing import Any, Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from src.automation.browser_manager import BrowserSession, translate_error
from src.automation.google_search_page import (
    CONSENT_BUTTON_LABELS,
    RESULT_SELECTORS,
    RESULT_TITLE_SELECTORS,
    TEXT_SAMPLE_CHARACTERS,
    PageKind,
    classify,
    pace_query,
)
from src.automation.models import SearchPageRead
from src.domain.exceptions import SearchError
from src.logging_config import get_logger
from src.services.safety_service import DomainSafetyService

log = get_logger(__name__)

CONSENT_CLICK_TIMEOUT_MS: Final = 2000
BLANK_PAGE: Final = "about:blank"

_READ_RESULTS: Final = """
([selectors, titleSelectors, limit]) => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
  for (const selector of selectors) {
    const blocks = Array.from(document.querySelectorAll(selector));
    if (blocks.length === 0) continue;
    const items = [];
    for (const block of blocks.slice(0, limit)) {
      const link = block.querySelector("a[href]");
      if (!link) continue;
      let title = "";
      for (const titleSelector of titleSelectors) {
        const node = block.querySelector(titleSelector);
        if (node) {
          title = clean(node.textContent);
          break;
        }
      }
      items.push([title || clean(link.textContent), link.href]);
    }
    return items;
  }
  return [];
}
"""

_PAGE_TEXT: Final = """
(limit) => (document.body ? (document.body.innerText || "").slice(0, limit) : "")
"""


class SearchPageActions:
    """Read one Google results page for one journey iteration."""

    def __init__(self, safety: DomainSafetyService) -> None:
        self._safety = safety

    async def read(self, session: BrowserSession, url: str, limit: int) -> SearchPageRead:
        """Open ``url``, take the result links, and blank the tab again.

        Raises :class:`~src.domain.exceptions.SafetyViolationError` when ``url`` is
        not a results page this application is allowed to read - refused before the
        browser is touched - and :class:`~src.domain.exceptions.SearchError` when
        Google answered with a wall, a consent screen it could not answer, or a
        page it no longer recognises.
        """
        target = self._safety.check_search_url(url).url
        await pace_query()
        page = session.page
        session.search_scope_open = True
        try:
            kind, text = await self._open_and_classify(page, target)
            if kind is PageKind.BLOCKED:
                log.warning("search_blocked_by_google", url=page.url)
                raise SearchError(
                    "Google answered with a human check instead of results, so the ranking "
                    "could not be read. Try again later, or set this scenario's rank source "
                    "to the Google Custom Search API."
                )
            if kind is not PageKind.SEARCH:
                raise SearchError(
                    "Google did not show a results page for this keyword "
                    f"(the browser ended up on '{page.url}')."
                )
            items = await self._collect(page, limit)
            return SearchPageRead(kind=kind.value, items=tuple(items), page_url=page.url, text=text)
        except PlaywrightError as exc:
            raise translate_error(exc, session, "reading the Google results page") from exc
        finally:
            await self._blank(page)
            session.search_scope_open = False

    # -- internals ---------------------------------------------------------------- #
    async def _open_and_classify(self, page: Page, target: str) -> tuple[PageKind, str]:
        """Navigate, answering the consent screen at most once, then say what we see."""
        await self._goto(page, target)
        kind, text = await self._classify(page)
        if kind is PageKind.CONSENT:
            if not await self._accept_consent(page):
                raise SearchError(
                    "Google asked for a consent decision before showing results and the "
                    "button could not be found. Open the browser profile once by hand and "
                    "answer the consent screen, or use the API channel."
                )
            await self._goto(page, target)
            kind, text = await self._classify(page)
        return kind, text

    async def _goto(self, page: Page, url: str) -> None:
        with contextlib.suppress(PlaywrightError):
            # A Google interstitial can fail to settle; the classification below
            # reads what is actually on screen and turns that into a clear error.
            await page.goto(url, wait_until="domcontentloaded")

    async def _classify(self, page: Page) -> tuple[PageKind, str]:
        text = await self._text(page)
        return classify(page.url, text), text

    async def _text(self, page: Page) -> str:
        try:
            value = await page.evaluate(_PAGE_TEXT, TEXT_SAMPLE_CHARACTERS)
        except PlaywrightError:
            return ""
        return value if isinstance(value, str) else ""

    async def _collect(self, page: Page, limit: int) -> list[tuple[str, str]]:
        args: list[Any] = [list(RESULT_SELECTORS), list(RESULT_TITLE_SELECTORS), max(1, limit)]
        try:
            raw = await page.evaluate(_READ_RESULTS, args)
        except PlaywrightError as exc:
            raise SearchError(f"The results page could not be read: {exc}") from exc
        if not isinstance(raw, list):
            return []
        return [
            (str(item[0]), str(item[1]))
            for item in raw
            if isinstance(item, (list, tuple)) and len(item) >= 2
        ]

    async def _accept_consent(self, page: Page) -> bool:
        """Click the least permissive consent button Google offers, if any."""
        for label in CONSENT_BUTTON_LABELS:
            for role in ("button", "link"):
                try:
                    locator = page.get_by_role(role, name=label).first
                    await locator.click(timeout=CONSENT_CLICK_TIMEOUT_MS)
                except PlaywrightError:
                    continue
                log.info("search_consent_answered", label=label)
                return True
        return False

    async def _blank(self, page: Page) -> None:
        """Leave no third-party page open once the data is in hand."""
        with contextlib.suppress(PlaywrightError):
            await page.goto(BLANK_PAGE)
