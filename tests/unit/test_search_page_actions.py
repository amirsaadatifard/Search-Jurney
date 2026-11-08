"""What the search step does to the browser - with a fake page, not a browser.

:class:`SearchPageActions` is the only code in the application that opens a search
engine, so its rules are pinned here: it asks the safety service first, it reads
one page, it touches nothing but a consent button, it reports a refusal instead of
working around it, and it leaves the tab on a blank page.

The scope points at an unreachable port on Google's own host, which is what lets
these tests exercise the real entry check and the real page classification without
a network: nothing here ever opens a socket.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from src.automation.google_search_page import PageKind
from src.automation.search_actions import SearchPageActions
from src.domain.exceptions import SafetyViolationError, SearchError
from src.services.safety_service import DomainSafetyService, SearchScope

GOOGLE = "https://www.google.com:9"
CONSENT = "https://consent.google.com:9"
SEARCH_URL = f"{GOOGLE}/search?q=cups"
BLOCKED_URL = f"{GOOGLE}/sorry/index"
ELSEWHERE = "https://elsewhere.example/taken-there"
SHOP_ITEMS = [["Shop", "https://shop.example/mugs"]]


class FakeLocator:
    """A button that exists only if the page was told about it."""

    def __init__(self, page: FakePage, name: str) -> None:
        self.page, self.name = page, name

    @property
    def first(self) -> FakeLocator:
        return self

    async def click(self, timeout: int = 0) -> None:
        if self.name not in self.page.buttons:
            raise PlaywrightError(f"Element not found: {self.name}")
        self.page.clicked.append(self.name)


class FakePage:
    """A page that behaves like the one Google sent back, without a browser."""

    def __init__(
        self,
        landings: list[str],
        text: str = "",
        items: list[list[str]] | None = None,
        buttons: tuple[str, ...] = (),
    ) -> None:
        self.landings = landings
        self.text = text
        self.items = items if items is not None else []
        self.buttons = buttons
        self.visited: list[str] = []
        self.clicked: list[str] = []
        self.evaluate_calls: list[Any] = []
        self.url = "about:blank"

    async def goto(self, url: str, wait_until: str = "") -> None:
        self.visited.append(url)
        if self.landings:
            self.url = self.landings.pop(0)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.evaluate_calls.append(arg)
        # The two scripts are told apart by their argument, as in any fake: a list is
        # "give me the results", a number is "give me the words on the page".
        return self.items if isinstance(arg, list) else self.text

    def get_by_role(self, role: str, name: str = "") -> FakeLocator:
        return FakeLocator(self, name)


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.blocked: list[Any] = []
        self.search_scope_open = False

    def navigation_violation(self) -> None:
        return None


def one_page(page: FakePage | None = None) -> tuple[SearchPageActions, FakeSession, FakePage]:
    """A session on a page that answers with one result."""
    fake = page or FakePage([SEARCH_URL], items=SHOP_ITEMS)
    return SearchPageActions(_SAFETY), FakeSession(fake), fake


# Shared because it holds no state: the same rules the application installs, only
# pointed at this test's address.
_SAFETY = DomainSafetyService(
    allowlist_provider=lambda: [],
    search_scope=SearchScope(entry=SEARCH_URL, assets=(GOOGLE, CONSENT)),
)


async def test_it_refuses_to_open_anything_but_a_results_page() -> None:
    service, session, page = one_page()
    with pytest.raises(SafetyViolationError):
        await service.read(session, "https://www.google.com:9/maps?q=cups", 10)
    assert page.visited == []


async def test_it_reads_the_links_and_blanks_the_tab_again() -> None:
    service, session, page = one_page()
    read = await service.read(session, SEARCH_URL, 10)
    assert read.kind == PageKind.SEARCH
    assert list(read.items) == [("Shop", "https://shop.example/mugs")]
    # One navigation, and the tab is left empty afterwards: nothing of Google's
    # stays open, and no result link is ever followed.
    assert page.visited == [SEARCH_URL, "about:blank"]
    assert session.search_scope_open is False


async def test_the_exception_for_google_assets_is_only_open_while_it_reads() -> None:
    """The guard's borrowed scope lasts one read, never the visit that follows it."""
    seen: list[bool] = []
    fake = FakePage([SEARCH_URL], items=SHOP_ITEMS)
    service, session, page = one_page(fake)
    original = page.evaluate

    async def spy(script: str, arg: Any = None) -> Any:
        seen.append(session.search_scope_open)
        return await original(script, arg)

    page.evaluate = spy  # type: ignore[method-assign]
    await service.read(session, SEARCH_URL, 10)
    assert seen and all(seen)
    assert session.search_scope_open is False


async def test_a_captcha_is_reported_and_never_worked_around() -> None:
    fake = FakePage([BLOCKED_URL], text="unusual traffic from your network")
    service, session, page = one_page(fake)
    with pytest.raises(SearchError, match="human check"):
        await service.read(session, SEARCH_URL, 10)
    assert page.visited == [SEARCH_URL, "about:blank"]  # no retry, no second attempt
    assert session.search_scope_open is False


async def test_a_consent_screen_is_answered_once_and_the_page_read_after_it() -> None:
    fake = FakePage(
        [CONSENT, SEARCH_URL],
        text="Before you continue",
        items=SHOP_ITEMS,
        buttons=("Reject all",),
    )
    service, session, page = one_page(fake)
    read = await service.read(session, SEARCH_URL, 10)
    assert read.kind == PageKind.SEARCH
    # The least permissive button, once, then the same page again - no other clicking.
    assert page.clicked == ["Reject all"]
    assert page.visited == [SEARCH_URL, SEARCH_URL, "about:blank"]


async def test_an_unanswerable_consent_screen_fails_the_session() -> None:
    fake = FakePage([CONSENT], text="Before you continue", buttons=())
    service, session, _ = one_page(fake)
    with pytest.raises(SearchError, match="consent"):
        await service.read(session, SEARCH_URL, 10)


async def test_a_page_that_is_not_the_results_page_is_not_mined_for_links() -> None:
    fake = FakePage([ELSEWHERE], items=SHOP_ITEMS)
    service, session, page = one_page(fake)
    with pytest.raises(SearchError, match="did not show a results page"):
        await service.read(session, SEARCH_URL, 10)
    assert not any(isinstance(arg, list) for arg in page.evaluate_calls)


async def test_the_requested_number_reaches_the_page_so_it_is_not_read_whole() -> None:
    service, session, page = one_page()
    await service.read(session, SEARCH_URL, 7)
    scripts = [arg for arg in page.evaluate_calls if isinstance(arg, list)]
    assert scripts and scripts[0][2] == 7
