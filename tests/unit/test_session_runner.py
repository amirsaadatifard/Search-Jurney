"""SessionRunner behaviour, verified against a fake browser and the real search walk.

The search step is the heart of a journey, so these tests hold it to account: the
keyword goes into Google's box, the position is whatever the page showed, the tab is
left on the site the result pointed at, and Google's own page is never stored. The
fake engine stands in for Chromium - a scripted set of result pages - while the
walk, the parser, the numbering, the allowlist and the state machine are the shipped
ones, so a broken journey fails here rather than on someone's desktop.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from src.automation.control import RunControl
from src.automation.engine import AutomationEngine
from src.automation.google_search_page import MAX_RESULTS, PageKind
from src.automation.interaction_service import ScheduledAction
from src.automation.models import (
    MODE_BROWSER,
    MODE_DIRECT,
    BrowserSettings,
    InteractionOutcome,
    ProxySettings,
    SearchPageRead,
    SessionPlan,
    SessionResult,
)
from src.automation.observer import RunObserver
from src.automation.recorder import SessionRecorder
from src.automation.search_journal import SearchJournal
from src.automation.search_walk import SNAPSHOT_RESULT_LIMIT, GoogleSearchWalk
from src.automation.session_runner import SessionRunner
from src.database.models import SearchObservation
from src.database.repositories import SessionRepository
from src.database.session import Database
from src.domain.enums import (
    Environment,
    EventType,
    ScreenshotKind,
    SessionState,
    SessionStatus,
)
from src.domain.exceptions import (
    BrowserLaunchError,
    NavigationError,
    SafetyViolationError,
    SearchError,
)
from src.services.safety_service import AllowedOrigin, DomainSafetyService

TARGET = "https://shop.example/"
GOOGLE_PAGE = "https://www.google.com/search"
Hook = Callable[[], Awaitable[None]]


def result(title: str, url: str, anchor: int) -> tuple[str, str, int]:
    """One ``(title, href, anchor index)`` triple, as a page read yields them."""
    return (title, url, anchor)


def page(*entries: tuple[str, str, int]) -> list[tuple[str, str, int]]:
    return list(entries)


#: A first page on which the approved site ranks second, after an ad-shaped link
#: that Google's own host carries and must not be counted as a result.
FIRST_PAGE = page(
    result("Ads are not results", "https://www.google.com/aclk?ad=1", 0),
    result("Ceramic Mug Studio", "https://other.example/mugs", 1),
    result("Handmade mugs | Shop", "https://shop.example/handmade-mugs", 2),
    result("Mug care", "https://shop.example/blog/care", 3),
)
SECOND_PAGE = page(
    result("Mug glossary", "https://other.example/glossary", 0),
    result("Studio diary", "https://other.example/diary", 1),
    result("Shop at the fair", "https://shop.example/fair", 2),
)
UNLISTED_PAGE = page(
    result("Ceramic Mug Studio", "https://other.example/mugs", 0),
    result("Mug care", "https://someone.example/care", 1),
)


class FakeEngine(AutomationEngine):
    """Scriptable browser: serves ``pages``, records everything the journey does.

    The reads come out of ``pages`` in order, so a journey that walks to page two
    gets page two - and a test that expects a walk can count the requests.
    """

    def __init__(self, pages: Sequence[Sequence[tuple[str, str, int]]] = (FIRST_PAGE,)) -> None:
        self.pages = [list(items) for items in pages]
        self.calls: list[str] = []
        self.opened: list[str] = []
        self.searches: list[int] = []
        self.next_requests: list[int] = []
        self.clicks: list[dict[str, Any]] = []
        self.scopes: list[str] = []
        self.fail_at: dict[str, Exception] = {}
        self.hooks: dict[str, Hook] = {}
        self.search_fails: Exception | None = None
        self.landing: str | None = None
        self.blocked: list[dict[str, Any]] = []
        self.previews = 0
        #: ``(page url, kind)`` of every capture: the evidence for "never photographed".
        self.shots: list[tuple[str, str]] = []
        self._url = ""
        self._open = False

    # -- plumbing ---------------------------------------------------------------- #
    @property
    def current_url(self) -> str:
        return self._url

    @property
    def is_open(self) -> bool:
        return self._open

    async def _step(self, name: str) -> None:
        self.calls.append(name)
        if name in self.hooks:
            await self.hooks[name]()
        if name in self.fail_at:
            raise self.fail_at[name]

    def _read(self, index: int, limit: int) -> SearchPageRead:
        items = tuple(self.pages[index])[:limit]
        return SearchPageRead(
            kind=PageKind.SEARCH.value,
            items=items,
            page_url=f"{GOOGLE_PAGE}?start={index * 10}",
            page_number=index + 1,
            has_next=index + 1 < len(self.pages),
            next_url=f"{GOOGLE_PAGE}?start={(index + 1) * 10}",
        )

    @property
    def screenshot_calls(self) -> list[str]:
        return [call for call in self.calls if call.startswith("screenshot:")]

    # -- the engine the journey talks to ----------------------------------------- #
    async def start_session(self, plan: SessionPlan) -> dict[str, Any]:
        await self._step("start_session")
        self._open = True
        return {"version": "fake-1.0", "network_fence": True}

    async def open_search_scope(self) -> None:
        await self._step("open_search_scope")
        self.scopes.append("open")

    async def close_search_scope(self) -> None:
        await self._step("close_search_scope")
        self.scopes.append("close")

    async def search(self, plan: SessionPlan, *, limit: int = MAX_RESULTS) -> SearchPageRead:
        await self._step("search")
        if self.search_fails is not None:
            raise self.search_fails
        self.searches.append(limit)
        return self._read(0, limit)

    async def next_results_page(
        self, plan: SessionPlan, read: SearchPageRead, *, limit: int = MAX_RESULTS
    ) -> SearchPageRead | None:
        await self._step("next_results_page")
        index = read.page_number
        self.next_requests.append(index)
        if index >= len(self.pages):
            return None
        return self._read(index, limit)

    async def click_result(self, result: Any, *, from_url: str = "") -> str:
        await self._step("click_result")
        self.clicks.append(
            {"position": result.position, "anchor": result.anchor_index, "from": from_url}
        )
        self._url = self.landing or result.url
        return self._url

    async def open_url(self, url: str) -> str:
        await self._step("open_url")
        self._url = url
        self.opened.append(url)
        return url

    async def perform_safe_interaction(
        self, action: ScheduledAction, rng: random.Random
    ) -> InteractionOutcome:
        await self._step("interaction")
        return InteractionOutcome(EventType.SCROLL.value, "scrolled down", {"y": 100})

    async def capture_screenshot(
        self, kind: ScreenshotKind, session_id: int, keyword: str
    ) -> dict[str, Any] | None:
        self.calls.append(f"screenshot:{kind.value}")
        self.shots.append((self._url, kind.value))
        return {"file": f"{kind.value}.png"}

    async def end_session(self) -> None:
        self.calls.append("end_session")
        self._open = False

    async def capture_preview(self) -> bytes | None:
        self.previews += 1
        return b"png" if self._open else None

    def drain_blocked(self) -> list[dict[str, Any]]:
        out, self.blocked = list(self.blocked), []
        return out

    async def close(self) -> None:
        self.calls.append("close")


@dataclass
class Recording(RunObserver):
    states: list[SessionState] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    ticks: list[tuple[float, float]] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    positions: list[int | None] = field(default_factory=list)
    finished: list[SessionResult] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    previews: list[bytes] = field(default_factory=list)

    def on_state_changed(self, state: SessionState, message: str) -> None:
        self.states.append(state)

    def on_event(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        self.events.append(event_type)

    def on_dwell_tick(self, remaining_seconds: float, planned_seconds: float) -> None:
        self.ticks.append((remaining_seconds, planned_seconds))

    def on_page_changed(self, url: str) -> None:
        self.pages.append(url)

    def on_target_position(self, position: int | None) -> None:
        self.positions.append(position)

    def on_session_finished(self, result: SessionResult) -> None:
        self.finished.append(result)

    def on_waiting(self, seconds: float) -> None:
        self.waits.append(seconds)

    def on_preview(self, image: bytes) -> None:
        self.previews.append(image)


class Broken(RunObserver):
    def on_state_changed(self, state: SessionState, message: str) -> None:
        raise RuntimeError("observer bug")

    def on_event(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        raise RuntimeError("observer bug")


def make_plan(**overrides: Any) -> SessionPlan:
    values: dict[str, Any] = {
        "scenario_id": None,
        "scenario_name": "Unit scenario",
        "keyword": "handmade ceramic mugs",
        "target_url": TARGET,
        "target_domain": "shop.example",
        "min_dwell_seconds": 0.5,
        "max_dwell_seconds": 0.5,
        "iterations": 1,
        "delay_between_iterations": 0,
        "scroll_enabled": False,
        "max_scroll_actions": 0,
        "clear_cookies": True,
        "clear_storage": True,
        "incognito": True,
        "browser": BrowserSettings(
            "Test", 1280, 800, True, "", "", False, True, True, "BROWSER_DEFAULT"
        ),
        "search_language": "en",
        "search_country": "us",
        "requested_results": 10,
        "max_search_pages": 5,
        "mode": MODE_BROWSER,
    }
    return SessionPlan(**(values | overrides))


@pytest.fixture
def safety() -> DomainSafetyService:
    return DomainSafetyService(allowlist_provider=lambda: [AllowedOrigin("shop.example")])


class Harness:
    """The runner wired the way the application wires it, minus Chromium."""

    def __init__(
        self,
        database: Database,
        safety: DomainSafetyService,
        pages: Sequence[Sequence[tuple[str, str, int]]] = (FIRST_PAGE,),
        *,
        journal: bool = False,
    ) -> None:
        self.database = database
        self.engine = FakeEngine(pages)
        self.control = RunControl()
        self.observer = Recording()
        self.safety = safety
        self.seed = 1234
        self.recorder = SessionRecorder(database)
        self.journal = SearchJournal(database) if journal else None

    def runner(self, observer: RunObserver | None = None) -> SessionRunner:
        return SessionRunner(
            self.engine,
            self.recorder,
            self.safety,
            self.control,
            observer if observer is not None else self.observer,
            search_walk=GoogleSearchWalk(),
            journal=self.journal,
            seed_source=lambda: self.seed,
        )

    def runner_without_search(self) -> SessionRunner:
        """A build with no search step at all - the misconfiguration to guard against."""
        return SessionRunner(
            self.engine,
            self.recorder,
            self.safety,
            self.control,
            self.observer,
            seed_source=lambda: self.seed,
        )

    async def run(self, plan: SessionPlan | None = None) -> Any:
        return await self.runner().run_plan(plan or make_plan())

    def sessions(self) -> list[Any]:
        with self.database.session_scope() as session:
            records = list(SessionRepository(session).search(with_details=True))
        return sorted(records, key=lambda record: record.id)

    def events(self, index: int = 0) -> list[EventType]:
        return [event.event_type for event in self.sessions()[index].events]


@pytest.fixture
def harness(database: Database, safety: DomainSafetyService) -> Harness:
    return Harness(database, safety)


# -- the journey ------------------------------------------------------------------------ #
async def test_a_journey_reads_the_page_it_was_shown_and_clicks_its_own_result(
    harness: Harness,
) -> None:
    summary = await harness.run()
    assert (summary.completed, summary.failed, summary.total) == (1, 0, 1)

    # The browser is up before Google is asked, and the click is the way in.
    assert harness.engine.calls[:5] == [
        "start_session",
        "open_search_scope",
        "search",
        "search",
        "next_results_page",
    ] or harness.engine.calls[:3] == ["start_session", "open_search_scope", "search"]
    assert harness.engine.clicks == [{"position": 2, "anchor": 2, "from": f"{GOOGLE_PAGE}?start=0"}]
    assert harness.engine.opened == []
    assert harness.engine.scopes == ["open", "close"]

    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.COMPLETED, SessionState.COMPLETED)
    # Three results, because Google's own ad link is not one of them: the count is
    # what a visitor would number, not what the DOM happened to contain.
    assert (record.target_found, record.target_position, record.results_count) == (
        True,
        2,
        3,
    )
    assert record.planned_dwell_seconds == 0.5
    assert 0.45 <= record.actual_dwell_seconds <= 0.9
    assert record.browser_info == {"version": "fake-1.0", "network_fence": True}
    assert harness.engine.calls[-2:] == ["end_session", "close"]


async def test_the_timeline_of_a_search_journey(harness: Harness) -> None:
    await harness.run()
    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.BROWSER_STARTED,
        EventType.KEYWORD_ENTERED,
        EventType.SEARCH_OBSERVED,
        EventType.TARGET_FOUND,
        EventType.RESULT_CLICKED,
        EventType.TARGET_OPENED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.DWELL_STARTED,
        EventType.DWELL_COMPLETED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.SESSION_COMPLETED,
    ]
    assert harness.observer.states == [
        SessionState.INITIALIZING,
        SessionState.OPENING_SEARCH,
        SessionState.ENTERING_KEYWORD,
        SessionState.SEARCHING,
        SessionState.ANALYZING_RESULTS,
        SessionState.TARGET_FOUND,
        SessionState.CLICKING_RESULT,
        SessionState.OPENING_TARGET,
        SessionState.DWELLING,
        SessionState.CAPTURING_RESULT,
        SessionState.COMPLETED,
    ]


async def test_every_event_carries_the_state_it_happened_in(harness: Harness) -> None:
    await harness.run()
    by_type = {event.event_type: event.state for event in harness.sessions()[0].events}
    assert by_type[EventType.SEARCH_OBSERVED] is SessionState.ANALYZING_RESULTS
    assert by_type[EventType.TARGET_FOUND] is SessionState.TARGET_FOUND
    assert by_type[EventType.RESULT_CLICKED] is SessionState.CLICKING_RESULT
    assert by_type[EventType.DWELL_STARTED] is SessionState.DWELLING
    assert by_type[EventType.SESSION_COMPLETED] is SessionState.COMPLETED


async def test_the_search_event_says_what_the_page_showed(harness: Harness) -> None:
    await harness.run()
    event = next(
        event
        for event in harness.sessions()[0].events
        if event.event_type is EventType.SEARCH_OBSERVED
    )
    assert event.data == {
        "provider": "google-browser",
        "observation_id": None,
        "position": 2,
        "result_count": 3,
        "pages_walked": 1,
    }
    # The message is written for a person reading a report, not for a parser.
    assert event.message == (
        "google-browser: 'handmade ceramic mugs' on shop.example at position 2 "
        "(of 3 results on the page)"
    )


async def test_a_session_that_never_left_the_first_page_reports_one_page_walked(
    harness: Harness,
) -> None:
    await harness.run()
    assert harness.engine.next_requests == []


# -- walking the result pages ------------------------------------------------------------ #
async def test_the_journey_walks_forward_until_the_target_turns_up(
    database: Database, safety: DomainSafetyService
) -> None:
    harness = Harness(database, safety, (UNLISTED_PAGE, SECOND_PAGE))
    summary = await harness.run(make_plan(requested_results=10))
    assert summary.completed == 1

    # Position 10 is "the second result of the second page" - the count runs across
    # the pages, exactly as it would for a person scrolling down the results.
    record = harness.sessions()[0]
    # Two filler results on page one, then the third link of page two: position 5.
    assert (record.target_found, record.target_position, record.results_count) == (True, 5, 5)
    assert harness.engine.next_requests == [1]
    assert harness.engine.clicks[0]["position"] == 5
    assert harness.observer.states.count(SessionState.PAGINATING_RESULTS) == 1
    assert harness.events().count(EventType.RESULTS_PAGED) == 1
    paged = next(e for e in record.events if e.event_type is EventType.RESULTS_PAGED)
    assert paged.data == {"page": 2}


async def test_the_search_event_says_how_many_pages_it_walked(
    database: Database, safety: DomainSafetyService
) -> None:
    harness = Harness(database, safety, (UNLISTED_PAGE, SECOND_PAGE))
    await harness.run()
    event = next(
        event
        for event in harness.sessions()[0].events
        if event.event_type is EventType.SEARCH_OBSERVED
    )
    assert event.data["pages_walked"] == 2
    assert "after 2 of 5 result pages" in event.message


async def test_the_walk_stops_at_the_planned_number_of_pages(harness: Harness) -> None:
    """A budget of one page is a promise to Google as much as a search setting."""
    many = [UNLISTED_PAGE] * 6 + [SECOND_PAGE]
    harness.engine.pages = many
    await harness.run(make_plan(max_search_pages=2))
    assert harness.engine.next_requests == [1]
    assert harness.sessions()[0].status is SessionStatus.TARGET_NOT_FOUND


async def test_a_page_that_offers_no_next_link_ends_the_walk(
    database: Database, safety: DomainSafetyService
) -> None:
    harness = Harness(database, safety, (UNLISTED_PAGE,))
    summary = await harness.run()
    assert summary.target_not_found == 1
    assert harness.engine.calls.count("next_results_page") == 0


# -- not listed ------------------------------------------------------------------------- #
async def test_a_site_the_results_do_not_contain_is_a_finding_not_an_error(
    harness: Harness,
) -> None:
    harness.engine.pages = [UNLISTED_PAGE]
    summary = await harness.run()
    assert (summary.target_not_found, summary.failed) == (1, 0)
    record = harness.sessions()[0]
    assert record.status is SessionStatus.TARGET_NOT_FOUND
    assert (record.target_found, record.target_position) == (False, None)
    assert record.state is SessionState.COMPLETED and record.error_type is None
    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.BROWSER_STARTED,
        EventType.KEYWORD_ENTERED,
        EventType.SEARCH_OBSERVED,
        EventType.TARGET_NOT_FOUND,
        EventType.SESSION_COMPLETED,
    ]
    # Google's page is read, never photographed, and the tab is blanked on the way out.
    assert harness.engine.screenshot_calls == []
    assert harness.engine.opened == []
    assert harness.engine.scopes == ["open", "close"]
    assert harness.observer.positions == [None]


async def test_a_journey_that_found_nothing_still_closes_the_scope_and_the_session(
    harness: Harness,
) -> None:
    """The ``finally`` that keeps the tab clean must run even when the walk fails."""
    harness.engine.pages = [UNLISTED_PAGE]
    await harness.run()
    assert harness.engine.calls[-2:] == ["end_session", "close"]
    assert harness.engine.calls.index("close_search_scope") > harness.engine.calls.index("search")


# -- the pages the journey must never touch ---------------------------------------------- #
async def test_google_is_never_photographed_even_though_the_journey_ends_with_a_shot(
    harness: Harness,
) -> None:
    """The only pages a session stores are the ones the *allowlist* covers.

    The search scope stays open until the click has landed, so the rule cannot be
    "no screenshots while the scope is open" - it is "never a page of the search
    engine", which is what the tab's own address says at the moment of capture.
    """
    await harness.run()
    assert harness.engine.screenshot_calls == ["screenshot:TARGET_OPENED", "screenshot:SESSION_END"]
    assert [url for url, _ in harness.engine.shots] == ["https://shop.example/handmade-mugs"] * 2
    assert all("google.com" not in url for url, _ in harness.engine.shots)


async def test_a_result_that_escapes_the_allowlist_fails_the_click(harness: Harness) -> None:
    """The landing page is checked; a click that ends up elsewhere is a failure."""
    harness.engine.fail_at["click_result"] = SafetyViolationError(
        "Navigation to a non-approved address was blocked."
    )
    summary = await harness.run()
    assert summary.failed == 1
    record = harness.sessions()[0]
    assert record.error_type == "SafetyViolationError"
    assert EventType.SAFETY_BLOCKED in harness.events()
    # One iteration, one failure, one picture of the page that went wrong.
    assert harness.engine.screenshot_calls == ["screenshot:ERROR"]


async def test_a_result_on_an_unapproved_subpage_of_the_site_is_still_visited(
    harness: Harness,
) -> None:
    """A blog on the approved host is the approved site: the click is fine."""
    harness.engine.pages = [
        page(result("Mug care", "https://shop.example/blog/care", 0)) + FIRST_PAGE[3:]
    ]
    await harness.run()
    assert harness.engine.clicks[0]["position"] == 1


# -- which page is opened --------------------------------------------------------------- #
async def test_the_page_google_listed_is_the_page_the_click_opens(harness: Harness) -> None:
    await harness.run()
    assert harness.engine.current_url == "https://shop.example/handmade-mugs"
    assert harness.observer.pages == ["https://shop.example/handmade-mugs"]
    assert EventType.SAFETY_BLOCKED not in harness.events()


async def test_a_listed_page_outside_the_allowlist_falls_back_to_the_approved_origin(
    harness: Harness,
) -> None:
    """Google may rank a host the user never approved; the journey visits the origin it may."""
    harness.engine.pages = [page(result("Shop", "https://other-shop.example/page", 0))]
    await harness.run(make_plan(target_domain="other-shop.example"))
    record = harness.sessions()[0]
    blocked = next(e for e in record.events if e.event_type is EventType.SAFETY_BLOCKED)
    assert blocked.data == {
        "blocked_url": "https://other-shop.example/page",
        "used_url": TARGET,
    }


async def test_every_result_the_page_yielded_carries_the_node_it_was_read_from(
    harness: Harness,
) -> None:
    """A numbered result is a clickable one, because both come from the same pass.

    A read that reports no anchor for a block still numbers it - by the position of
    the link the page listed - so a journey can never end up holding "position 4"
    with nothing to click. That fallback is what makes the click safe to plan.
    """
    harness.engine.pages = [[("Handmade mugs", TARGET, 7)]]
    await harness.run()
    assert harness.engine.clicks == [{"position": 1, "anchor": 7, "from": f"{GOOGLE_PAGE}?start=0"}]
    assert harness.sessions()[0].target_position == 1


async def test_the_landing_url_of_the_click_is_what_gets_recorded(harness: Harness) -> None:
    """Google's wrapper redirects; the record says where the tab ended up."""
    harness.engine.landing = "https://shop.example/handmade-mugs?utm_source=google"
    await harness.run()
    event = next(
        e
        for e in harness.sessions()[0].events
        if e.event_type is EventType.RESULT_CLICKED  # the URL the tab reports
    )
    assert event.data["url"] == "https://shop.example/handmade-mugs?utm_source=google"
    assert event.data["href"] == "https://shop.example/handmade-mugs"


# -- what the page showed, stored --------------------------------------------------------- #
async def test_the_snapshot_holds_the_page_the_journey_read(database: Database) -> None:
    allowed = [AllowedOrigin("shop.example")]
    long_page = FIRST_PAGE + page(
        *[(f"Filler {n}", f"https://filler{n}.example/page", n + 3) for n in range(1, 11)]
    )
    harness = Harness(database, DomainSafetyService(lambda: list(allowed)), (long_page,))
    await harness.run(make_plan(requested_results=40))
    snapshot = harness.sessions()[0].settings_snapshot
    assert snapshot["observed_provider"] == "google-browser"
    assert snapshot["observed_result_count"] == 13
    assert snapshot["observed_pages"] == 1
    assert snapshot["mode"] == MODE_BROWSER
    assert snapshot["max_search_pages"] == 5
    # A long page is stored up to the limit, which is what keeps a session row
    # readable in the UI; the full list stays in the observation.
    assert len(snapshot["observed_results"]) == SNAPSHOT_RESULT_LIMIT
    assert snapshot["observed_results"][0] == {
        "position": 1,
        "title": "Ceramic Mug Studio",
        "url": "https://other.example/mugs",
    }
    assert snapshot["seed"] == 1234


async def test_the_journal_stores_the_observation_the_session_points_at(
    database: Database, safety: DomainSafetyService
) -> None:
    """The observation is the evidence: one row for the page, one per result."""
    harness = Harness(database, safety, (FIRST_PAGE,), journal=True)
    await harness.run()
    record = harness.sessions()[0]
    assert record.observation_id is not None
    with database.session_scope() as session:
        observation = session.get(SearchObservation, record.observation_id)
        assert observation is not None
        assert (observation.query, observation.provider) == (
            "handmade ceramic mugs",
            "google-browser",
        )
        assert (observation.target_found, observation.target_position) == (True, 2)
        assert [result.position for result in observation.results] == [1, 2, 3]
        # What is stored is what the page contained, Google's own links excluded.
        assert observation.results[0].url == "https://other.example/mugs"


async def test_a_walk_that_found_the_target_on_page_two_stores_both_pages(
    database: Database, safety: DomainSafetyService
) -> None:
    harness = Harness(database, safety, (UNLISTED_PAGE, SECOND_PAGE), journal=True)
    await harness.run()
    record = harness.sessions()[0]
    with database.session_scope() as session:
        observation = session.get(SearchObservation, record.observation_id)
        assert observation is not None
        assert observation.target_position == 5
        assert [result.position for result in observation.results][-2:] == [4, 5]


# -- direct visits ------------------------------------------------------------------------ #
async def test_a_direct_journey_never_opens_a_search_engine(harness: Harness) -> None:
    summary = await harness.run(make_plan(mode=MODE_DIRECT))
    assert (summary.completed, summary.failed) == (1, 0)
    assert harness.engine.searches == [] and harness.engine.clicks == []
    assert harness.engine.scopes == []
    assert harness.engine.opened == [TARGET]
    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.TARGET_FOUND,
        EventType.BROWSER_STARTED,
        EventType.TARGET_OPENED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.DWELL_STARTED,
        EventType.DWELL_COMPLETED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.SESSION_COMPLETED,
    ]
    record = harness.sessions()[0]
    assert (record.target_found, record.target_position, record.results_count) == (
        True,
        None,
        None,
    )
    # A local target has no ranking to claim, so no position is reported at all.
    assert harness.observer.positions == []


async def test_a_direct_journey_starts_the_browser_under_its_own_state(harness: Harness) -> None:
    await harness.run(make_plan(mode=MODE_DIRECT))
    assert harness.observer.states == [
        SessionState.INITIALIZING,
        SessionState.TARGET_FOUND,
        SessionState.STARTING_BROWSER,
        SessionState.OPENING_TARGET,
        SessionState.DWELLING,
        SessionState.CAPTURING_RESULT,
        SessionState.COMPLETED,
    ]


@pytest.mark.parametrize(
    ("environment", "listed"),
    [
        (Environment.LOCAL, False),
        (Environment.CONTROLLED_TEST, True),
        (Environment.STAGING, True),
        (Environment.DEVELOPMENT, True),
    ],
)
def test_only_a_site_google_cannot_list_skips_the_search_step(
    environment: Environment, listed: bool
) -> None:
    """A local target is visited; every other environment has to be searched for.

    Asking Google to rank something on this computer would spend a query to learn
    what is already known, and would make the bundled demo unusable without a key -
    so a local site is the one case where no ranking is claimed.
    """
    from src.automation.plan_factory import skips_rank_observation
    from src.database.models import TargetSite

    site = TargetSite(
        display_name="Site",
        base_url="https://shop.example/",
        domain="shop.example",
        environment=environment,
        approved_for_automation=True,
    )
    assert skips_rank_observation(site) is (not listed)


# -- failures --------------------------------------------------------------------------- #
async def test_a_refusal_by_google_fails_the_session_without_a_finding(harness: Harness) -> None:
    """A blocked page is not "not listed": a session that could not look claims nothing."""
    harness.engine.search_fails = SearchError("Google answered with a robot check.")
    summary = await harness.run(make_plan(iterations=2))
    assert (summary.failed, summary.completed) == (2, 0)
    record = harness.sessions()[0]
    assert record.error_type == "SearchError"
    assert "robot check" in record.error_message
    assert (record.target_found, record.target_position) == (None, None)
    assert harness.engine.opened == []
    # Two iterations, each refused, each with its own evidence.
    assert harness.engine.screenshot_calls == ["screenshot:ERROR", "screenshot:ERROR"]
    # The scope that was opened is closed even though the journey died inside it.
    assert harness.engine.scopes == ["open", "close", "open", "close"]  # one pair per try
    assert harness.events()[-1] is EventType.SESSION_FAILED


async def test_a_missing_search_step_is_reported_as_a_configuration_problem(
    harness: Harness,
) -> None:
    """No search step means no answer, and an invented position would be worse than none."""
    summary = await harness.runner_without_search().run_plan(make_plan())
    assert summary.failed == 1
    record = harness.sessions()[0]
    assert record.error_type == "InvalidConfigurationError"
    assert "search step" in record.error_message
    assert harness.engine.opened == []
    # The scope was opened for the step and closed by the ``finally`` that has to
    # run whatever the search step does with it.
    assert harness.engine.scopes == ["open", "close"]


async def test_a_page_that_cannot_be_opened_is_reported_as_navigation_failure(
    harness: Harness,
) -> None:
    harness.engine.fail_at["open_url"] = NavigationError("the page did not load")
    summary = await harness.run(make_plan(iterations=3, mode=MODE_DIRECT))
    assert (summary.failed, summary.completed, summary.total) == (3, 0, 3)
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.FAILED, SessionState.FAILED)
    assert (record.error_type, record.error_message) == ("NavigationError", "the page did not load")
    assert record.target_found is True and record.target_position is None
    assert harness.engine.calls.count("end_session") == 3


async def test_recovery_after_a_failed_iteration(harness: Harness) -> None:
    attempts = {"n": 0}

    async def flaky() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NavigationError("first one fails")

    harness.engine.hooks["click_result"] = flaky
    summary = await harness.run(make_plan(iterations=2))
    assert (summary.failed, summary.completed) == (1, 1)
    assert [s.status for s in harness.sessions()] == [SessionStatus.FAILED, SessionStatus.COMPLETED]


async def test_an_unexpected_exception_does_not_crash_the_runner(harness: Harness) -> None:
    harness.engine.search_fails = ZeroDivisionError("bug")
    summary = await harness.run(make_plan(iterations=2))
    assert summary.failed == 2
    record = harness.sessions()[0]
    assert record.error_type == "ZeroDivisionError" and "Unexpected error" in record.error_message


async def test_a_browser_that_will_not_launch_aborts_the_remaining_iterations(
    harness: Harness,
) -> None:
    harness.engine.fail_at["start_session"] = BrowserLaunchError("Chromium is missing")
    summary = await harness.run(make_plan(iterations=5))
    assert (summary.failed, summary.total) == (1, 1)
    assert harness.engine.calls.count("start_session") == 1


async def test_an_unapproved_target_is_blocked_before_google_is_asked(harness: Harness) -> None:
    summary = await harness.run(make_plan(target_url="https://not-approved.example/"))
    assert summary.failed == 1
    assert harness.engine.searches == []  # no query is spent on a site the user may not automate
    assert not {"start_session", "open_url"} & set(harness.engine.calls)
    record = harness.sessions()[0]
    assert record.error_type == "SafetyViolationError"
    assert EventType.SAFETY_BLOCKED in harness.events()


async def test_a_public_search_engine_can_never_be_a_target(harness: Harness) -> None:
    summary = await harness.run(make_plan(target_url="https://www.google.com/"))
    assert summary.failed == 1 and harness.engine.searches == []
    assert "public search engine" in harness.sessions()[0].error_message


async def test_approval_revoked_between_iterations_stops_the_next_one(database: Database) -> None:
    allowed = [AllowedOrigin("shop.example")]
    harness = Harness(database, DomainSafetyService(lambda: list(allowed)))

    first = await harness.runner().run_iteration(make_plan(iterations=2), 1, "run")
    assert first.status is SessionStatus.COMPLETED
    allowed.clear()
    second = await harness.runner().run_iteration(make_plan(iterations=2), 2, "run")
    assert second.status is SessionStatus.FAILED and second.error_type == "SafetyViolationError"


# -- dwell, interactions and observers ---------------------------------------------------- #
async def test_scrolls_are_performed_and_counted(harness: Harness) -> None:
    plan = make_plan(
        min_dwell_seconds=1.2, max_dwell_seconds=1.2, scroll_enabled=True, max_scroll_actions=4
    )
    await harness.run(plan)
    events = harness.events()
    scrolls = events.count(EventType.SCROLL)
    assert scrolls >= 2
    assert harness.engine.calls.count("interaction") == scrolls
    assert harness.sessions()[0].scroll_actions == scrolls
    interacting = [
        e.state for e in harness.sessions()[0].events if e.event_type is EventType.SCROLL
    ]
    assert set(interacting) == {SessionState.INTERACTING}
    assert SessionState.INTERACTING in harness.observer.states


async def test_scroll_disabled_means_no_interactions(harness: Harness) -> None:
    await harness.run(make_plan(scroll_enabled=False, max_scroll_actions=9))
    assert "interaction" not in harness.engine.calls


async def test_a_failing_interaction_ends_the_session(harness: Harness) -> None:
    harness.engine.fail_at["interaction"] = SafetyViolationError("the page left the approved scope")
    plan = make_plan(
        min_dwell_seconds=1.2, max_dwell_seconds=1.2, scroll_enabled=True, max_scroll_actions=4
    )
    summary = await harness.run(plan)
    assert summary.failed == 1
    assert harness.sessions()[0].error_type == "SafetyViolationError"


async def test_the_observer_follows_the_run(harness: Harness) -> None:
    await harness.run()
    observer = harness.observer
    assert observer.states[0] is SessionState.INITIALIZING
    assert observer.states[-1] is SessionState.COMPLETED
    assert observer.positions == [2]
    assert observer.pages == ["https://shop.example/handmade-mugs"]
    assert observer.ticks[0] == (0.5, 0.5) and observer.ticks[-1][0] == 0
    assert [result.status for result in observer.finished] == [SessionStatus.COMPLETED]
    assert observer.previews, "a live view is expected while a page is open"


async def test_a_broken_observer_cannot_break_the_run(harness: Harness) -> None:
    summary = await harness.runner(Broken()).run_plan(make_plan())
    assert summary.completed == 1


async def test_the_same_seed_gives_the_same_dwell_and_schedule(harness: Harness) -> None:
    plan = make_plan(
        min_dwell_seconds=0.2, max_dwell_seconds=0.9, scroll_enabled=True, max_scroll_actions=3
    )
    for _ in range(2):
        await harness.runner().run_iteration(plan, 1, "run")
    first, second = (record.settings_snapshot for record in harness.sessions())
    assert first["chosen_dwell_seconds"] == second["chosen_dwell_seconds"]
    assert first["scheduled_actions"] == second["scheduled_actions"]


async def test_the_snapshot_makes_a_session_reproducible(harness: Harness) -> None:
    plan = make_plan(
        scroll_enabled=True,
        max_scroll_actions=4,
        min_dwell_seconds=1,
        max_dwell_seconds=1,
        proxy=ProxySettings("lab", "HTTP", "10.0.0.1", 3128, "user", "s3cret"),
    )
    harness.control.stop()  # only the first session row matters here
    await harness.runner().run_iteration(plan, 1, "run-x")
    snapshot = harness.sessions()[0].settings_snapshot
    assert snapshot["seed"] == 1234
    assert snapshot["keyword"] == "handmade ceramic mugs"
    assert snapshot["max_scroll_actions"] == 4
    assert snapshot["proxy"]["host"] == "10.0.0.1"
    assert "password" not in snapshot["proxy"]
    assert "s3cret" not in str(snapshot)


# -- the fence's news reaches the timeline ------------------------------------------------- #
async def test_requests_the_guard_blocked_are_written_as_events(harness: Harness) -> None:
    """A silent block would be a lie by omission, so the run reports every one."""
    harness.engine.blocked = [{"url": "https://tracker.example/pixel.png", "reason": "not_allowed"}]
    await harness.run()
    blocked = [
        event
        for event in harness.sessions()[0].events
        if event.event_type is EventType.SAFETY_BLOCKED
    ]
    assert any("tracker.example" in event.message for event in blocked)
    assert harness.engine.blocked == []  # drained, so nothing is reported twice


# -- stop and pause ---------------------------------------------------------------------- #
async def test_a_stop_during_the_dwell_ends_the_session_gracefully(harness: Harness) -> None:
    async def stop_when_opened() -> None:
        asyncio.get_running_loop().call_later(0.3, harness.control.stop)

    harness.engine.hooks["click_result"] = stop_when_opened
    plan = make_plan(min_dwell_seconds=20, max_dwell_seconds=20, iterations=3)
    started = time.monotonic()
    summary = await harness.run(plan)
    assert time.monotonic() - started < 3  # did not wait out the 20 s dwell
    assert (summary.stopped, summary.total) == (1, 1)
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.STOPPED, SessionState.STOPPED)
    assert record.finished_at is not None and record.error_type is None
    assert harness.events()[-1] is EventType.SESSION_STOPPED
    assert harness.engine.calls[-2:] == ["end_session", "close"]
    # A stopped journey still leaves Google's tab behind cleanly.
    assert harness.engine.scopes == ["open", "close"]


async def test_a_stop_after_the_search_never_clicks(harness: Harness) -> None:
    """The stop is honoured at the next safe boundary, which is before the click."""

    async def stop_now() -> None:
        harness.control.stop()

    harness.engine.hooks["search"] = stop_now
    summary = await harness.run(make_plan(min_dwell_seconds=20, max_dwell_seconds=20))
    assert summary.stopped == 1
    assert "click_result" not in harness.engine.calls


async def test_a_stop_before_the_start_runs_nothing(harness: Harness) -> None:
    harness.control.stop()
    summary = await harness.run(make_plan(iterations=3))
    assert summary.total == 0 and harness.sessions() == []
    assert harness.engine.calls == ["close"]


async def test_a_stop_during_the_delay_between_iterations(harness: Harness) -> None:
    asyncio.get_running_loop().call_later(1.0, harness.control.stop)
    plan = make_plan(iterations=3, delay_between_iterations=30)
    started = time.monotonic()
    summary = await harness.run(plan)
    assert time.monotonic() - started < 4
    assert (summary.completed, summary.total) == (1, 1)
    assert harness.observer.waits == [30.0]


async def test_pause_and_resume_do_not_count_as_dwell_time(harness: Harness) -> None:
    plan = make_plan(min_dwell_seconds=1.0, max_dwell_seconds=1.0)

    async def pause_resume() -> None:
        await asyncio.sleep(0.4)
        harness.control.pause()
        await asyncio.sleep(1.0)
        harness.control.resume()

    task = asyncio.create_task(pause_resume())
    started = time.monotonic()
    await harness.run(plan)
    wall = time.monotonic() - started
    await task

    record = harness.sessions()[0]
    assert record.status is SessionStatus.COMPLETED
    assert 0.95 <= record.actual_dwell_seconds <= 1.4  # the paused minute is not dwell
    assert wall >= 1.9  # but the run really did wait
    events = harness.events()
    assert events.count(EventType.PAUSED) == 1 and events.count(EventType.RESUMED) == 1
    assert events.index(EventType.PAUSED) < events.index(EventType.RESUMED)


async def test_a_pause_waits_for_the_click_it_interrupted(harness: Harness) -> None:
    """A pause is honoured at a boundary, not in the middle of a page.

    The request arrives while the result is being clicked, so the pause is taken at
    the next checkpoint - the state change after the click - and the session then
    finishes as if it had never been interrupted.
    """

    async def pause_now() -> None:
        harness.control.pause()
        asyncio.get_running_loop().call_later(0.3, harness.control.resume)

    harness.engine.hooks["click_result"] = pause_now
    await harness.run(make_plan(min_dwell_seconds=1.0, max_dwell_seconds=1.0))
    states = harness.observer.states
    assert states.index(SessionState.PAUSED) == states.index(SessionState.CLICKING_RESULT) + 1
    assert states[-1] is SessionState.COMPLETED


async def test_a_stop_while_paused(harness: Harness) -> None:
    async def pause_then_stop() -> None:
        await asyncio.sleep(0.3)
        harness.control.pause()
        await asyncio.sleep(0.5)
        harness.control.stop()

    task = asyncio.create_task(pause_then_stop())
    summary = await harness.run(make_plan(min_dwell_seconds=30, max_dwell_seconds=30))
    await task
    assert summary.stopped == 1
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.STOPPED, SessionState.STOPPED)
    assert EventType.PAUSED in harness.events()


async def test_revoked_approval_during_the_dwell_fails_the_session_at_once(
    database: Database,
) -> None:
    allowed = [AllowedOrigin("shop.example")]
    harness = Harness(database, DomainSafetyService(lambda: list(allowed)))

    async def revoke() -> None:
        asyncio.get_running_loop().call_later(0.3, allowed.clear)

    harness.engine.hooks["click_result"] = revoke
    started = time.monotonic()
    summary = await harness.run(make_plan(min_dwell_seconds=30, max_dwell_seconds=30))
    assert time.monotonic() - started < 3
    assert summary.failed == 1
    record = harness.sessions()[0]
    assert record.error_type == "SafetyViolationError" and record.state is SessionState.FAILED
    assert EventType.SAFETY_BLOCKED in harness.events()
    assert EventType.DWELL_COMPLETED not in harness.events()


# -- several iterations ------------------------------------------------------------------- #
async def test_iterations_are_numbered_and_share_a_run_id(harness: Harness) -> None:
    summary = await harness.runner().run_plan(replace(make_plan(iterations=3)), run_id="run-42")
    assert summary.completed == 3
    sessions = harness.sessions()
    assert [session.iteration for session in sessions] == [1, 2, 3]
    assert {session.total_iterations for session in sessions} == {3}
    assert {session.run_id for session in sessions} == {"run-42"}
    assert (
        harness.engine.calls.count("start_session") == 3
        and harness.engine.calls.count("close") == 1
    )
    # Every iteration asks Google again: the ranking is a fresh reading, not a cached one.
    assert harness.engine.calls.count("search") == 3
