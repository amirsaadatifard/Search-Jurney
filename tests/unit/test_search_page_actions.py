"""What the search step does to the browser - with a fake page, not a browser.

:class:`SearchPageActions` is the only code in the application that opens a search
engine, so its rules are pinned here: the keyword goes into the page's own box and
only into it, the results are read from the same pass that numbers them, a click is
made on the anchor that was read, a refusal is reported instead of worked around,
and the tab is left clean for whatever comes next.

The fake page answers with URLs and element counts the way a rendered page would,
so these tests say exactly what the shipped code asks Chromium for - no browser, no
network, and no question asked of Google.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from src.automation.browser_manager import BrowserSession
from src.automation.google_search_page import (
    GOOGLE_HOME,
    KEYWORD_FIELD_SELECTORS,
    NEXT_PAGE_SELECTORS,
    RESULT_ANCHOR_SELECTORS,
    RESULT_SELECTORS,
    PageKind,
    SearchSite,
    normalize_result_link,
    set_pacing_gap,
)
from src.automation.search_actions import SearchPageActions
from src.domain.exceptions import SafetyViolationError, SearchError
from src.services.safety_service import AllowedOrigin, DomainSafetyService, SearchScope

SITE = "https://www.google.com:9"
FIRST_RESULTS = f"{SITE}/search?q=cups"
SECOND_RESULTS = f"{SITE}/search?q=cups&start=10"
BLOCKED_URL = f"{SITE}/sorry/index"
CONSENT_URL = f"{SITE}/consent"
SHOP = "https://shop.example/mugs"
ELSEWHERE = "https://elsewhere.example/taken-there"

#: Every selector the page is ever asked about, so one landing can answer them all.
_ALL_SELECTORS: tuple[str, ...] = (
    *KEYWORD_FIELD_SELECTORS,
    *RESULT_SELECTORS,
    *RESULT_ANCHOR_SELECTORS,
    *NEXT_PAGE_SELECTORS,
    "role=button",
)


class FakeElement:
    """One node on the page: it records what was done to it and can move the tab.

    The box, the next-page control and the consent button are built once per page, so
    the node a test inspects afterwards is the very object the code was handed -
    which is the only way to prove it typed into *that* box and clicked *that* link.
    """

    def __init__(self, page: FakePage, name: str, index: int = -1, **attributes: Any) -> None:
        self.page = page
        self.name = name
        self.index = index
        self.attributes = dict(attributes)
        self.done: list[tuple[str, str]] = []

    @property
    def href(self) -> str:
        if 0 <= self.index < len(self.page.results):
            return str(self.page.results[self.index][1])
        return str(self.attributes.get("href", ""))

    async def click(self, timeout: int = 0) -> None:
        self.done.append(("click", self.name))
        if self.attributes.get("ends_wall"):
            # Answering the consent screen sends the tab back where it came from,
            # and the wall stays answered - which is what the journey bets on.
            self.page.consent_answered = True
            self.page.arrive(self.page.origin)
            return
        if self.name == "search box":
            self.page.arrive(self.page.results_url_for(self.page.typed))
            return
        if self.index >= 0:  # an anchor goes where its own href points
            self.page.arrive(self.page.anchor_for(self.index))
            return
        target = self.attributes.get("navigate_to")
        if target:
            self.page.arrive(target)

    async def fill(self, value: str, timeout: int = 0) -> None:
        self.done.append(("fill", value))
        self.page.typed = ""

    async def type(self, value: str, delay: int = 0) -> None:
        self.done.append(("type", value))
        self.page.typed = value

    async def press(self, key: str, timeout: int = 0) -> None:
        self.done.append(("press", key))
        # A search box submits on Enter; that is the whole bet of this step.
        if key == "Enter" and self.page.submits:
            self.page.arrive(self.page.results_url_for(self.page.typed))

    async def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        self.done.append(("scroll", self.name))

    async def get_attribute(self, name: str, timeout: int = 0) -> str | None:
        if name == "href" and self.index >= 0:
            return self.href
        return self.attributes.get(name)


class FakeLocator:
    """A selector answered with the elements the fake page currently holds."""

    def __init__(self, name: str, elements: list[FakeElement]) -> None:
        self.name = name
        self.list = elements

    @property
    def first(self) -> FakeElement:
        if not self.list:
            raise PlaywrightError(f"No element matches {self.name}")
        return self.list[0]

    def nth(self, index: int) -> FakeElement:
        if index >= len(self.list):
            raise PlaywrightError(f"No element {index} for {self.name}")
        return self.list[index]

    async def count(self) -> int:
        return len(self.list)

    async def get_attribute(self, name: str, timeout: int = 0) -> str | None:
        return self.first.get_attribute(name, timeout)

    async def click(self, timeout: int = 0) -> None:
        await self.first.click(timeout=timeout)


class FakePage:
    """A page that behaves like the one the search engine sent back.

    Three knobs decide what it looks like: ``wall`` (nothing, a cookie wall, a robot
    check), ``results`` (the ``(title, href, anchor)`` triples a read of the DOM
    returns) and ``has_next``. Which selector matches, and where a click goes, follow
    from those - the same way they follow from a real rendered page.
    """

    def __init__(
        self,
        url: str = "",
        *,
        results: list[list[Any]] | None = None,
        text: str = "",
        wall: str = "",
        has_next: bool = True,
        goto_fails: Exception | None = None,
        anchor_target: str | None = None,
    ) -> None:
        self.results: list[list[Any]] = (
            [["Shop", SHOP, 0]] if results is None else [list(item) for item in results]
        )
        self.text = text
        self.wall = wall
        self.has_next = has_next
        self.goto_fails = goto_fails
        #: Where an anchor really goes, whatever its href says (a test's shortcut).
        self.anchor_target: str | None = anchor_target
        #: Whether the box reacts to Enter at all.
        self.submits = True
        #: Whether the tab ignores a navigation entirely (a page that never moved).
        self.frozen = False
        self.typed = ""
        self.visited: list[str] = []
        self.settle_called = False
        self.consent_answered = False
        self._url = url
        self._box = FakeElement(self, "search box")
        self._next = FakeElement(self, "next", href=SECOND_RESULTS, navigate_to=SECOND_RESULTS)
        self._consent = FakeElement(self, "consent button", label="Reject all", ends_wall=True)
        self._nodes: dict[str, list[FakeElement]] = {}
        if url:
            self._nodes = {selector: self._build(selector) for selector in _ALL_SELECTORS}

    @property
    def url(self) -> str:
        return self._url

    @property
    def origin(self) -> str:
        return SITE

    def results_url_for(self, query: str) -> str:
        text = "+".join(query.split()) or "cups"
        return f"{SITE}/search?q={text}"

    def anchor_for(self, index: int) -> str:
        """Where clicking result ``index`` really takes the tab."""
        if self.anchor_target is not None:
            return self.anchor_target
        return str(self.results[index][1])

    # -- the tab's own behaviour ---------------------------------------------------- #
    def arrive(self, url: str, *, follow_wall: bool = True) -> None:
        """Move the tab, letting a standing wall intercept the way Google's does."""
        before = self._url
        if follow_wall and self.wall == "consent" and not self.consent_answered:
            self._url = CONSENT_URL
        elif follow_wall and self.wall == "sorry":
            self._url = BLOCKED_URL
        else:
            self._url = url
        if self.frozen:
            self._url = before
            return
        self.visited.append(self._url)
        self._nodes = {selector: self._build(selector) for selector in _ALL_SELECTORS}

    def on_wall(self) -> bool:
        return self._url == CONSENT_URL

    def _build(self, selector: str) -> list[FakeElement]:
        """Which of its standing nodes a selector matches at this landing."""
        if self.on_wall():
            return [self._consent] if selector == "role=button" else []
        if selector in KEYWORD_FIELD_SELECTORS:
            return [self._box]
        reading = "/search" in self._url
        if selector in RESULT_SELECTORS:
            if not reading:
                return []
            return [FakeElement(self, f"result {n}", index=n) for n in range(len(self.results))]
        if selector in RESULT_ANCHOR_SELECTORS:
            if not reading:
                return []
            return [FakeElement(self, f"anchor {n}", index=n) for n in range(len(self.results))]
        if selector in NEXT_PAGE_SELECTORS:
            return [self._next] if reading and self.has_next and self.results else []
        return []

    # -- what the actions ask for --------------------------------------------------- #
    async def goto(self, url: str, **kwargs: Any) -> None:
        if self.goto_fails is not None:
            self.visited.append(url)
            self._url = url
            raise self.goto_fails
        self.arrive(url)

    def elements_for(self, selector: str) -> list[FakeElement]:
        return self._nodes.get(selector, self._build(selector))

    def node_count(self, selector: str) -> int:
        return len(self.elements_for(selector))

    def node(self, selector: str, index: int = 0) -> FakeElement:
        """A test's window onto one node: the same instance the code is handed."""
        return self.elements_for(selector)[index]

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(selector, self.elements_for(selector))

    def get_by_role(self, role: str, name: str = "") -> FakeLocator:
        found = [
            element
            for element in self.elements_for(f"role={role}")
            if not name or element.attributes.get("label") == name
        ]
        return FakeLocator(f"{role}[name={name}]", found)

    async def click(self, selector: str, timeout: int = 0) -> None:
        """Click the first element a selector matches, as ``Page.click`` does."""
        found = self.elements_for(selector)
        if not found:
            raise PlaywrightError(f"No element matches {selector}")
        await found[0].click(timeout=timeout)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if "innerText" in script:  # the body-text probe
            return self.text
        return self.results if "/search" in self._url else []

    async def wait_for_timeout(self, milliseconds: float) -> None:
        pass

    async def wait_for_load_state(self, state: str = "", timeout: int = 0) -> None:
        self.settle_called = True


def make_actions() -> SearchPageActions:
    """The shipped actions, aimed at a fake origin, with the shipped safety rules.

    The pacing gap is switched off here and nowhere else: these tests are about what
    the code asks the page for, and a courtesy wait is the one thing they need not
    repeat.
    """
    set_pacing_gap(0.0)
    return SearchPageActions(
        DomainSafetyService(
            allowlist_provider=lambda: [AllowedOrigin("shop.example")],
            search_scope=SearchScope(entry=f"{SITE}/", pages=("/", "/search", "/consent")),
        ),
        site=SearchSite(entry=f"{SITE}/"),
        timeout_ms=1000,
        settle_seconds=0.05,
    )


def make_session(page: FakePage, actions: SearchPageActions) -> BrowserSession:
    """A session whose guard predicate is the one the browser manager installs."""
    safety = actions._safety  # the same service the engine was handed, on purpose
    return BrowserSession(
        None,  # type: ignore[arg-type] - no context is touched by these paths
        page,  # type: ignore[arg-type]
        search_allowed=safety.is_search_origin,
    )


@pytest.fixture
def actions() -> SearchPageActions:
    return make_actions()


# -- the scope ---------------------------------------------------------------------- #
async def test_the_scope_is_a_flag_the_search_step_turns_on_and_off(
    actions: SearchPageActions,
) -> None:
    page = FakePage(GOOGLE_HOME)
    session = make_session(page, actions)
    await actions.open_scope(session)
    assert session.search_scope_open is True
    await actions.close_scope(session)
    assert session.search_scope_open is False
    # Leaving Google means leaving it: the tab is blanked so nothing later reads it.
    assert page.visited == ["about:blank"]


async def test_closing_the_scope_leaves_a_page_that_is_not_googles_alone(
    actions: SearchPageActions,
) -> None:
    """After a result click the tab holds the target site, and must keep holding it."""
    page = FakePage(SHOP)
    session = make_session(page, actions)
    session.search_scope_open = True
    await actions.close_scope(session)
    assert page.visited == []  # nothing was navigated
    assert session.search_scope_open is False


async def test_a_tab_that_cannot_be_blanked_still_closes_the_scope(
    actions: SearchPageActions,
) -> None:
    """Housekeeping must not be able to break a session."""

    class Stubborn(FakePage):
        async def goto(self, url: str, **kwargs: Any) -> None:
            raise PlaywrightError("detached while navigating")

    page = Stubborn(GOOGLE_HOME)
    session = make_session(page, actions)
    await actions.open_scope(session)
    await actions.close_scope(session)
    assert session.search_scope_open is False


# -- typing the keyword --------------------------------------------------------------- #
async def test_the_keyword_goes_into_the_pages_own_box_and_the_results_follow(
    actions: SearchPageActions,
) -> None:
    page = FakePage(GOOGLE_HOME)
    read = await actions.search(make_session(page, actions), "cups")
    box = page.node(KEYWORD_FIELD_SELECTORS[0])
    assert ("type", "cups") in box.done and ("press", "Enter") in box.done
    assert page.url == FIRST_RESULTS
    assert read.kind == PageKind.SEARCH.value
    assert read.page_number == 1 and read.has_next is True
    assert read.items == (("Shop", SHOP, 0),)


async def test_the_first_page_is_reached_from_the_entry_page_of_the_configured_site(
    actions: SearchPageActions,
) -> None:
    """The step opens what the guard was configured with - not a hardcoded host."""
    page = FakePage(GOOGLE_HOME)
    await actions.search(make_session(page, actions), "cups")
    assert page.visited[0] == f"{SITE}/"


async def test_a_page_without_a_box_asks_for_the_results_url_instead(
    actions: SearchPageActions,
) -> None:
    """A layout the selectors do not know still gets its results - by URL."""

    class NoBox(FakePage):
        def _build(self, selector: str) -> list[FakeElement]:
            if selector in KEYWORD_FIELD_SELECTORS:
                return []
            return super()._build(selector)

    page = NoBox(GOOGLE_HOME)
    read = await actions.search(make_session(page, actions), "cups")
    assert page.node_count(KEYWORD_FIELD_SELECTORS[0]) == 0
    assert page.visited == [f"{SITE}/", FIRST_RESULTS]
    assert read.page_number == 1 and read.items  # the fallback still yields results


async def test_a_box_that_produces_no_results_falls_back_to_the_url(
    actions: SearchPageActions,
) -> None:
    page = FakePage(GOOGLE_HOME)
    page.submits = False  # typed, Enter pressed, nothing happened
    read = await actions.search(make_session(page, actions), "cups")
    assert read.items and page.visited[-1] == FIRST_RESULTS


async def test_a_cookie_wall_is_answered_before_the_keyword_is_typed(
    actions: SearchPageActions,
) -> None:
    """The one button this application presses on Google's page is "Reject all"."""
    page = FakePage(GOOGLE_HOME, wall="consent")
    read = await actions.search(make_session(page, actions), "cups")
    assert page.consent_answered is True
    assert page.url == FIRST_RESULTS
    assert read.items  # the search went on to happen: a wall is not a refusal


# -- what the page refuses to be ------------------------------------------------------- #
async def test_a_robot_check_is_reported_as_a_refusal_and_never_worked_around(
    actions: SearchPageActions,
) -> None:
    page = FakePage(GOOGLE_HOME, wall="sorry")
    with pytest.raises(SearchError) as caught:
        await actions.search(make_session(page, actions), "cups")
    assert "robot check" in caught.value.message
    assert "captcha" in caught.value.message.lower()
    # It stopped at the wall: nothing was typed and no second page was asked for.
    assert page.visited == [BLOCKED_URL]
    assert page.node(KEYWORD_FIELD_SELECTORS[0]).done == []


async def test_a_page_that_is_not_a_results_page_fails_with_what_it_actually_is(
    actions: SearchPageActions,
) -> None:
    page = FakePage(f"{SITE}/somethingelse")
    page.submits = False
    page.results = []
    page.frozen = True  # nothing the journey asks for moves the tab off this page
    with pytest.raises(SearchError) as caught:
        await actions.search(make_session(page, actions), "cups")
    assert caught.value.context["kind"] == PageKind.OTHER.value


async def test_a_results_page_with_no_links_says_the_layout_changed(
    actions: SearchPageActions,
) -> None:
    page = FakePage(FIRST_RESULTS, results=[])
    with pytest.raises(SearchError, match="No result links"):
        await actions.search(make_session(page, actions), "cups")


async def test_a_navigation_that_chromium_refuses_keeps_its_own_words(
    actions: SearchPageActions,
) -> None:
    page = FakePage(goto_fails=PlaywrightError("net::ERR_CONNECTION_REFUSED\n  at ..."))
    with pytest.raises(SearchError) as caught:
        await actions.search(make_session(page, actions), "cups")
    assert "could not open" in caught.value.message
    assert caught.value.context["browser_error"] == "net::ERR_CONNECTION_REFUSED"


# -- walking -------------------------------------------------------------------------- #
async def test_the_next_page_is_reached_by_clicking_the_pages_own_control(
    actions: SearchPageActions,
) -> None:
    page = FakePage(FIRST_RESULTS)
    session = make_session(page, actions)
    read = await actions.search(session, "cups")
    following = await actions.next_page(session, read, query="cups", page_number=1, limit=10)
    assert following is not None
    assert read.page_number == 1
    assert page.url == SECOND_RESULTS
    assert following.page_number == 2


async def test_no_next_control_means_the_walk_ends_without_a_guess(
    actions: SearchPageActions,
) -> None:
    """A page that offers nothing is answered with ``None``, not with the same page."""
    page = FakePage(FIRST_RESULTS, has_next=False)
    session = make_session(page, actions)
    read = await actions.search(session, "cups")
    assert await actions.next_page(session, read, query="cups", page_number=1) is None


async def test_a_next_page_that_is_not_results_fails_the_session(
    actions: SearchPageActions,
) -> None:
    page = FakePage(FIRST_RESULTS)
    session = make_session(page, actions)
    read = await actions.search(session, "cups")
    page.wall = "sorry"  # the next control leads to the wall, not to page two
    with pytest.raises(SearchError, match="robot check"):
        await actions.next_page(session, read, query="cups", page_number=1)


# -- clicking through ------------------------------------------------------------------ #
async def test_the_result_is_clicked_on_the_anchor_that_was_read(
    actions: SearchPageActions,
) -> None:
    page = FakePage(FIRST_RESULTS)
    session = make_session(page, actions)
    await actions.open_scope(session)
    landed = await actions.click_result(session, href=SHOP, anchor_index=0, position=1)
    assert landed == SHOP
    assert page.settle_called  # the tab was given a moment, as a person needs
    assert page.url == SHOP
    # The journey navigated by clicking only: nothing was opened by URL here.
    assert page.visited[-1] == SHOP


async def test_a_result_with_no_anchor_of_its_own_is_not_clicked(
    actions: SearchPageActions,
) -> None:
    page = FakePage(FIRST_RESULTS)
    with pytest.raises(SearchError, match="no clickable link"):
        await actions.click_result(
            make_session(page, actions), href=SHOP, anchor_index=-1, position=4
        )


async def test_a_page_that_changed_under_the_journey_is_reported_not_guessed(
    actions: SearchPageActions,
) -> None:
    """Position 12 cannot be clicked on a page that now holds one result."""
    page = FakePage(FIRST_RESULTS)
    with pytest.raises(SearchError, match="changed before the click"):
        await actions.click_result(
            make_session(page, actions), href=SHOP, anchor_index=11, position=12
        )


async def test_a_click_that_never_left_google_is_an_error_not_a_visit(
    actions: SearchPageActions,
) -> None:
    """An interstitial that swallows the click must not become a dwell on Google."""
    page = FakePage(FIRST_RESULTS, anchor_target=FIRST_RESULTS)
    session = make_session(page, actions)
    await actions.open_scope(session)
    with pytest.raises(SearchError, match="did not leave Google"):
        await actions.click_result(session, href=SHOP, anchor_index=0, position=1)


async def test_a_result_that_escapes_the_allowlist_is_refused(
    actions: SearchPageActions,
) -> None:
    """The click is checked where it lands, so a wrapper cannot smuggle a host in."""
    page = FakePage(FIRST_RESULTS, results=[["Shop", ELSEWHERE, 0]], anchor_target=ELSEWHERE)
    session = make_session(page, actions)
    await actions.open_scope(session)
    with pytest.raises(SafetyViolationError, match="approved scope"):
        await actions.click_result(session, href=ELSEWHERE, anchor_index=0, position=1)


async def test_a_google_redirect_wrapper_is_read_as_the_page_it_points_at(
    actions: SearchPageActions,
) -> None:
    """What is numbered and clicked is the destination, not the wrapper."""
    wrapped = f"{SITE}/url?q=https://shop.example/mugs"
    page = FakePage(FIRST_RESULTS, results=[["Shop", wrapped, 0]], anchor_target=SHOP)
    read = await actions.search(make_session(page, actions), "cups")
    assert read.items == (("Shop", wrapped, 0),)  # the raw href, as the page wrote it
    assert normalize_result_link(read.items[0][1]) == SHOP
