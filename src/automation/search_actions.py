"""The one place the browser touches Google.

:class:`SearchPageActions` speaks to Chromium and nothing else: it opens Google's
start page in the tab, types the scenario's keyword into Google's own search box
and presses Enter, reads the result links out of the rendered page, clicks the
"next" control when the journey has to walk further, and clicks the result that
belongs to the approved site. It has no idea what a scenario is and keeps no
state between calls - the walk itself lives in
:mod:`src.automation.google_search_page`, which is pure and testable offline.

Two rules make the module trustworthy:

*  **A failure is never swallowed.** A navigation Chromium rejects is reported
   with the browser's own first line of explanation and the URL the tab ended up
   on. ``--debug`` prints every step, so a stuck journey can be diagnosed from
   the log without guessing.
*  **A click is only ever made on the node that was read.** The result list and
   the click target come from the same DOM pass, identified by the anchor's index
   among the result blocks - the module cannot be talked into opening an arbitrary
   URL.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Sequence
from typing import Any, Final

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.automation.browser_manager import BrowserSession, translate_error
from src.automation.google_search_page import (
    CONSENT_BUTTON_LABELS,
    KEYWORD_FIELD_SELECTORS,
    MAX_RESULTS,
    NEXT_PAGE_SELECTORS,
    RESULT_ANCHOR_SELECTORS,
    RESULT_SELECTORS,
    RESULT_TITLE_SELECTORS,
    TEXT_SAMPLE_CHARACTERS,
    PageKind,
    SearchSite,
    blocked_message,
    looks_blocked,
    next_page_number,
    normalize_result_link,
    pace_query,
)
from src.automation.models import SearchPageRead
from src.domain.exceptions import SafetyViolationError, SearchError
from src.logging_config import get_logger
from src.services.safety_service import DomainSafetyService

log = get_logger(__name__)

BLANK_PAGE: Final = "about:blank"
CONSENT_CLICK_TIMEOUT_MS: Final = 2000
TYPING_DELAY_MS: Final = 40
POLL_SECONDS: Final = 0.25

#: What a page is checked against when it has to be described in an error: a
#: handful of selector counts says more than any stack trace.
_SNAPSHOT_SELECTORS: Final = (("result blocks", RESULT_SELECTORS), ("anchors", ("a[href]",)))


class SearchPageActions:
    """Drive one Google search in one tab."""

    def __init__(
        self,
        safety: DomainSafetyService,
        *,
        site: SearchSite | None = None,
        timeout_ms: int = 30000,
        settle_seconds: float = 6.0,
    ) -> None:
        self._safety = safety
        self._site = site or SearchSite()
        self._timeout_ms = max(timeout_ms, 1000)
        self._settle_seconds = max(settle_seconds, 1.0)

    # -- scope -------------------------------------------------------------------- #
    @staticmethod
    async def open_scope(session: BrowserSession) -> None:
        session.search_scope_open = True
        log.debug("search_scope_opened", url=session.page.url)

    @staticmethod
    async def close_scope(session: BrowserSession, *, blank_if_google: bool = True) -> None:
        """End the exception and, unless the journey already clicked through, clean the tab."""
        should_blank = blank_if_google and session.allows_search_origin(session.page.url)
        try:
            if should_blank:
                await _goto(session.page, BLANK_PAGE, timeout_ms=5000, why="leave Google")
        except PlaywrightError as exc:
            log.debug("search_tab_not_blanked", error=_first_line(exc))
        finally:
            session.search_scope_open = False
            log.debug("search_scope_closed")

    # -- opening the search ------------------------------------------------------- #
    async def search(
        self,
        session: BrowserSession,
        query: str,
        *,
        language: str = "",
        country: str = "",
        per_page: int = 10,
        limit: int = MAX_RESULTS,
        first_page: bool = True,
    ) -> SearchPageRead:
        """Show page one of the results for ``query``, by typing it if at all possible.

        With ``first_page`` the journey opens Google's start page, finds the search
        box, types the keyword and presses Enter. When that box cannot be found or
        the page does not react, the same results page is opened by URL instead -
        the fallback is reported in the debug log, and the returned read says which
        route was taken in ``page_url``.
        """
        await pace_query()
        page = session.page
        if first_page:
            await self._goto(page, self._site.entry, why="open the search page")
            kind = await self._classify(page)
            if kind is PageKind.CONSENT:
                await self._accept_consent(page)
                kind = await self._classify(page)
            if kind is PageKind.BLOCKED:
                raise self._blocked_error(page.url)  # a wall is answered, a refusal is not
            if kind is not PageKind.SEARCH:
                if await self._type_keyword(page, query):
                    return await self._finish_read(session, page, limit)
                log.debug("keyword_box_unusable", fallback="results url", query=query)
        return await self._open_results_url(
            session,
            query,
            page_number=1,
            language=language,
            country=country,
            per_page=per_page,
            limit=limit,
        )

    async def _type_keyword(self, page: Page, query: str) -> bool:
        """Type ``query`` into Google's own box and press Enter; ``False`` if that fails."""
        selector = await self._wait_for(page, KEYWORD_FIELD_SELECTORS, "search box")
        if selector is None:
            log.debug("search_box_missing", **await self._snapshot(page))
            return False
        try:
            box = page.locator(selector).first
            await box.click(timeout=min(self._timeout_ms, 5000))
            await box.fill("")
            await box.type(query, delay=TYPING_DELAY_MS)
            await box.press("Enter")
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            log.warning(
                "keyword_typing_failed",
                selector=selector,
                query=query,
                error=_first_line(exc),
                url=page.url,
            )
            return False
        log.debug("keyword_typed", selector=selector, query=query, url=page.url)
        if await self._wait_for(page, RESULT_SELECTORS, "results"):
            return True
        log.debug("typed_without_results", url=page.url, **await self._snapshot(page))
        return False

    async def _open_results_url(
        self,
        session: BrowserSession,
        query: str,
        *,
        page_number: int,
        language: str,
        country: str,
        per_page: int,
        limit: int,
    ) -> SearchPageRead:
        """Open one page of results by URL - the route used when clicking is not possible."""
        url = self._site.results_url(query, language, country, per_page, page_number)
        if page_number > 1:
            await pace_query()
        await self._goto(session.page, url, why=f"results page {page_number}")
        return await self._finish_read(session, session.page, limit, page_number=page_number)

    # -- walking ------------------------------------------------------------------ #
    async def next_page(
        self,
        session: BrowserSession,
        read: SearchPageRead,
        *,
        query: str,
        page_number: int,
        language: str = "",
        country: str = "",
        per_page: int = 10,
        limit: int = MAX_RESULTS,
    ) -> SearchPageRead | None:
        """Walk to the next result page, by clicking Google's own control.

        ``None`` means Google offered no next page: that ends the walk, and the
        session reports "not in the first *n* pages" honestly rather than guessing.
        """
        page = session.page
        control = await self._wait_for(page, NEXT_PAGE_SELECTORS, "next-page control", timeout=1.5)
        if control is None:
            log.debug("next_page_missing", url=page.url)
            return None
        before = page.url
        try:
            await page.click(control, timeout=min(self._timeout_ms, 8000))
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            log.warning(
                "next_page_click_failed",
                selector=control,
                error=_first_line(exc),
                url=page.url,
                fallback="results url",
            )
            return await self._open_results_url(
                session,
                query,
                page_number=page_number + 1,
                language=language,
                country=country,
                per_page=per_page,
                limit=limit,
            )
        if page.url == before:
            raise SearchError(
                f"Clicking '{control}' did not change the page ({before})", url=before
            )
        if await self._wait_for(page, RESULT_SELECTORS, "the next page's results") is None:
            kind = await self._classify(page)
            if kind is PageKind.BLOCKED:
                raise self._blocked_error(page.url)
            raise SearchError(
                f"The page after '{control}' is not a results page", url=page.url, kind=kind.value
            )
        number = next_page_number(page.url, page_number, per_page) or (page_number + 1)
        read = await self._finish_read(session, page, limit, page_number=number)
        return read

    # -- clicking through --------------------------------------------------------- #
    async def click_result(
        self,
        session: BrowserSession,
        *,
        href: str,
        anchor_index: int,
        position: int,
        from_url: str = "",
    ) -> str:
        """Click the result the page showed and return the URL the tab landed on.

        The click is made on the anchor at ``anchor_index`` in the same list the
        results were read from. Nothing is navigated by URL here.
        """
        page = session.page
        if anchor_index < 0:
            raise SearchError(
                f"Result {position} has no clickable link in the page that was read", url=page.url
            )
        selector = await self._wait_for(page, RESULT_ANCHOR_SELECTORS, "result links", timeout=2.0)
        if selector is None:
            raise SearchError(
                "The results page vanished before the journey could click it", url=page.url
            )
        anchors = page.locator(selector)
        try:
            count = await anchors.count()
        except PlaywrightError as exc:
            raise translate_error(exc, session, "counting result links") from exc
        if count <= anchor_index:
            raise SearchError(
                f"The results page changed before the click: it now has {count} result links, "
                f"so result {position} cannot be clicked",
                url=page.url,
                expected_index=anchor_index,
                found=count,
            )
        anchor = anchors.nth(anchor_index)
        try:
            await anchor.scroll_into_view_if_needed(timeout=min(self._timeout_ms, 5000))
            await anchor.click(timeout=min(self._timeout_ms, 8000))
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            raise translate_error(exc, session, f"clicking result {position}") from exc
        await _settle(page, self._settle_seconds)
        landed = normalize_result_link(page.url) or page.url
        log.debug("result_clicked", position=position, href=href, landed_on=landed)
        # Still on a page of the search engine means the result never took us
        # anywhere - an interstitial, an unanswered consent wall, a redirect that
        # bounced back. Saying so beats dwelling on Google's own page.
        if self._safety.is_search_page(landed):
            raise SearchError(
                f"Result {position} did not leave Google's own pages (the tab is on {landed})",
                url=landed,
            )
        try:
            self._safety.check_redirect(from_url or page.url, landed)
        except SafetyViolationError as exc:
            raise SafetyViolationError(
                f"Clicking result {position} left the approved scope: {exc.message}",
                reason="redirect_blocked",
                url=landed,
            ) from exc
        return landed

    # -- reading one page --------------------------------------------------------- #
    async def _finish_read(
        self,
        session: BrowserSession,
        page: Page,
        limit: int,
        *,
        page_number: int = 1,
    ) -> SearchPageRead:
        kind = await self._classify(page)
        if kind is PageKind.BLOCKED:
            raise self._blocked_error(page.url)
        if kind is not PageKind.SEARCH:
            raise SearchError(
                f"Google answered with a page that is not a results page ({kind.value})",
                url=page.url,
                kind=kind.value,
                **await self._snapshot(page),
            )
        items = await self._collect(page, limit)
        has_next, next_href = await self._next_page(page)
        text = await self._body_text(page)
        if not items and looks_blocked(text):
            raise self._blocked_error(page.url)
        if not items:
            raise SearchError(
                f"No result links could be read from Google's page (page {page_number})",
                url=page.url,
                page_number=page_number,
                **await self._snapshot(page),
            )
        read = SearchPageRead(
            kind=kind.value,
            items=tuple(items),
            page_url=page.url,
            text=text,
            page_number=page_number,
            has_next=has_next,
            next_url=next_href,
        )
        log.debug(
            "results_read",
            page_number=page_number,
            results=len(items),
            has_next=has_next,
            url=page.url,
        )
        return read

    async def _collect(self, page: Page, limit: int) -> list[tuple[str, str, int]]:
        """``(title, href, anchor_index)`` for every result block on the page."""
        args: list[Any] = [
            list(RESULT_SELECTORS),
            list(RESULT_TITLE_SELECTORS),
            max(1, min(limit, MAX_RESULTS)),
        ]
        try:
            raw = await page.evaluate(_READ_RESULTS, args)
        except PlaywrightError as exc:
            raise SearchError(
                f"The results page could not be read: {_first_line(exc)}", url=page.url
            ) from exc
        if not isinstance(raw, list):
            return []
        return [
            (str(item[0]), str(item[1]), int(item[2]))
            for item in raw
            if isinstance(item, (list, tuple)) and len(item) >= 3
        ]

    async def _next_page(self, page: Page) -> tuple[bool, str]:
        for selector in NEXT_PAGE_SELECTORS:
            try:
                if await page.locator(selector).count() == 0:
                    continue
                href = await page.locator(selector).first.get_attribute(
                    "href", timeout=CONSENT_CLICK_TIMEOUT_MS
                )
                return True, href or ""
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
        return False, ""

    async def _accept_consent(self, page: Page) -> bool:
        """Click the least permissive consent button Google offers, if any."""
        for label in CONSENT_BUTTON_LABELS:
            for role in ("button", "link"):
                try:
                    await page.get_by_role(role, name=label).first.click(
                        timeout=CONSENT_CLICK_TIMEOUT_MS
                    )
                except (PlaywrightError, PlaywrightTimeoutError):
                    continue
                log.info("search_consent_answered", label=label, url=page.url)
                await _settle(page, 2.0)
                return True
        raise SearchError(
            "Google asked for a consent decision before showing any results and the button "
            "could not be clicked, so the journey stopped. Open google.com once by hand in "
            "this browser profile and answer the consent screen.",
            url=page.url,
        )

    # -- page inspection ---------------------------------------------------------- #
    # -- internals ---------------------------------------------------------------- #
    async def _goto(self, page: Page, url: str, *, why: str) -> None:
        """Navigate and let Chromium's own explanation reach the caller verbatim."""
        log.debug("search_navigating", why=why, url=url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        except PlaywrightError as exc:
            landed = await _current_url(page)
            reason = _first_line(exc) or type(exc).__name__
            log.error(
                "search_navigation_failed", why=why, url=url, landed_on=landed, browser_error=reason
            )
            raise SearchError(
                f"Chromium could not open {url} — {reason}", url=landed or url, browser_error=reason
            ) from exc
        kind = await self._classify(page)
        log.debug("search_navigated", why=why, landed_on=page.url, kind=kind.value)

    async def _classify(self, page: Page) -> PageKind:
        """What kind of page the tab holds, judged by the site it belongs to."""
        return self._site.classify(page.url, await self._body_text(page))

    async def _body_text(self, page: Page) -> str:
        try:
            value = await page.evaluate(_PAGE_TEXT, TEXT_SAMPLE_CHARACTERS)
        except PlaywrightError:
            return ""
        return value if isinstance(value, str) else ""

    async def _wait_for(
        self, page: Page, selectors: Sequence[str], what: str, *, timeout: float | None = None
    ) -> str | None:
        """Return the first selector that matches, waiting up to the settle time."""
        deadline = time.monotonic() + (self._settle_seconds if timeout is None else timeout)
        while True:
            for selector in selectors:
                try:
                    if await page.locator(selector).count() > 0:
                        log.debug("search_element_found", what=what, selector=selector)
                        return selector
                except (PlaywrightError, PlaywrightTimeoutError):
                    continue
            if time.monotonic() >= deadline:
                log.debug("search_element_missing", what=what, url=page.url)
                return None
            with contextlib.suppress(PlaywrightError, PlaywrightTimeoutError):
                await page.wait_for_timeout(POLL_SECONDS * 1000)

    async def _snapshot(self, page: Page) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, selectors in _SNAPSHOT_SELECTORS:
            total = 0
            for selector in selectors:
                try:
                    total += await page.locator(selector).count()
                except PlaywrightError:
                    continue
            out[name] = total
        return out

    def _blocked_error(self, url: str) -> SearchError:
        return SearchError(blocked_message(url), url=url, kind=PageKind.BLOCKED.value)


def _first_line(exc: Exception) -> str:
    """Chromium's own explanation, on one line - the part worth reading in a log."""
    text = str(exc).strip()
    return text.splitlines()[0][:400] if text else type(exc).__name__


async def _goto(page: Page, url: str, *, timeout_ms: int, why: str) -> None:
    """Module-level goto for the housekeeping paths, which must not raise."""
    with contextlib.suppress(PlaywrightError):
        await page.goto(url, timeout=timeout_ms)


async def _current_url(page: Page) -> str:
    try:
        return page.url
    except PlaywrightError:
        return ""


async def _settle(page: Page, seconds: float) -> None:
    """Give the tab a moment, the way a person needs one after a click."""
    with contextlib.suppress(PlaywrightError):
        await page.wait_for_load_state("load", timeout=int(seconds * 1000))


_READ_RESULTS: Final = """
([selectors, titleSelectors, limit]) => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const anchorIndex = new Map(anchors.map((node, index) => [node, index]));
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
      const index = anchorIndex.get(link);
      items.push([title || clean(link.textContent), link.href, index === undefined ? -1 : index]);
    }
    return items;
  }
  return [];
}
"""

_PAGE_TEXT: Final = """
(limit) => (document.body ? (document.body.innerText || "").slice(0, limit) : "")
"""
