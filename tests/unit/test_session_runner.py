"""SessionRunner behaviour, verified against a fake engine and a fake rank observer.

The ranking is the first step of every journey, so the fake rank observer is the
piece that decides whether a session opens a browser at all - which is exactly
what these tests are about. No browser and no network are involved.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from src.automation.control import RunControl
from src.automation.engine import AutomationEngine
from src.automation.interaction_service import ScheduledAction
from src.automation.models import (
    MODE_DIRECT,
    MODE_GOOGLE,
    BrowserSettings,
    InteractionOutcome,
    ProxySettings,
    RankObservation,
    ResultItem,
    RunSummary,
    SessionPlan,
    SessionResult,
)
from src.automation.observer import RunObserver
from src.automation.rank_observer import RankObserver
from src.automation.recorder import SessionRecorder
from src.automation.session_runner import SNAPSHOT_RESULT_LIMIT, SessionRunner
from src.database.repositories import SessionRepository
from src.database.session import Database
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus
from src.domain.exceptions import BrowserLaunchError, GoogleQuotaError, NavigationError
from src.services.safety_service import AllowedOrigin, DomainSafetyService

TARGET = "https://shop.example/"
Hook = Callable[[], Awaitable[None]]


class FakeRankObserver:
    """Answers with a scripted ranking; ``error`` makes the provider itself fail.

    It is a ``RankObserver`` by shape, not by inheritance, so a missing member of
    the protocol shows up here as a failing test rather than a silent ``None``.
    """

    def __init__(
        self,
        position: int | None = 3,
        *,
        result_count: int = 10,
        target_url: str = TARGET,
        observation_id: int | None = 77,
        error: Exception | None = None,
    ) -> None:
        self.position = position
        self.result_count = result_count
        self.target_url = target_url
        self.observation_id = observation_id
        self.error = error
        self.calls: list[SessionPlan] = []
        self.on_answer: Callable[[], None] | None = None

    @property
    def provider_name(self) -> str:
        return "google-custom-search"

    @property
    def credentials_ready(self) -> bool:
        return self.error is None

    async def observe(self, plan: SessionPlan) -> RankObservation:
        self.calls.append(plan)
        if self.error is not None:
            raise self.error
        if self.on_answer is not None:
            self.on_answer()
        return RankObservation(
            provider=self.provider_name,
            position=self.position,
            result_count=self.result_count,
            target_url=self.target_url,
            observation_id=self.observation_id,
            results=tuple(
                ResultItem(n, f"Other {n}", f"https://site{n}.example/page")
                for n in range(1, self.result_count + 1)
            ),
        )


class FakeEngine(AutomationEngine):
    """Scriptable engine: records calls, can fail at a step or run a hook there."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.opened: list[str] = []
        self.fail_at: dict[str, Exception] = {}
        self.hooks: dict[str, Hook] = {}
        self._url = ""
        self._open = False

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

    async def start_session(self, plan: SessionPlan) -> dict[str, Any]:
        await self._step("start_session")
        self._open = True
        return {"version": "fake-1.0"}

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
        return {"file": f"{kind.value}.png"}

    async def end_session(self) -> None:
        self.calls.append("end_session")
        self._open = False

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
    }
    return SessionPlan(**(values | overrides))


def direct_plan(**overrides: Any) -> SessionPlan:
    """A plan that skips the ranking step (a Google Research visit, or a local site)."""
    values: dict[str, Any] = {
        "mode": MODE_DIRECT,
        "provider": "google-custom-search",
        "observation_id": 42,
        "observed_position": 6,
        "observed_result_count": 10,
    }
    return make_plan(**(values | overrides))


@pytest.fixture
def safety() -> DomainSafetyService:
    return DomainSafetyService(allowlist_provider=lambda: [AllowedOrigin("shop.example")])


class Harness:
    def __init__(self, database: Database, safety: DomainSafetyService) -> None:
        self.database = database
        self.engine = FakeEngine()
        self.rank = FakeRankObserver()
        self.control = RunControl()
        self.observer = Recording()
        self.safety = safety
        self.seed = 1234
        self.queries: list[str] = []

    def runner(self, observer: RunObserver | None = None) -> SessionRunner:
        return self._runner(observer, self.rank)

    def runner_without_provider(self) -> SessionRunner:
        """A run with no search provider wired in - the misconfiguration to guard against."""
        return self._runner(None, None)

    def _runner(self, observer: RunObserver | None, rank: RankObserver | None) -> SessionRunner:
        return SessionRunner(
            self.engine,
            SessionRecorder(self.database),
            self.safety,
            self.control,
            observer if observer is not None else self.observer,
            rank_observer=rank,
            seed_source=lambda: self.seed,
        )

    async def run(self, plan: SessionPlan | None = None) -> RunSummary:
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


# -- the ranking step --------------------------------------------------------------- #
async def test_ranking_is_read_before_the_browser_starts(harness: Harness) -> None:
    summary = await harness.run()
    assert (summary.completed, summary.failed, summary.total) == (1, 0, 1)

    assert [plan.keyword for plan in harness.rank.calls] == ["handmade ceramic mugs"]
    states = harness.observer.states
    assert states.index(SessionState.SEARCHING) < states.index(SessionState.STARTING_BROWSER)
    assert "start_session" in harness.engine.calls


async def test_the_observer_receives_the_query_options_of_the_plan(harness: Harness) -> None:
    await harness.run(make_plan(search_language="de", search_country="de", requested_results=20))
    plan = harness.rank.calls[0]
    assert (plan.search_language, plan.search_country, plan.requested_results) == ("de", "de", 20)


async def test_successful_session_records_the_full_timeline(harness: Harness) -> None:
    await harness.run()
    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.SEARCH_OBSERVED,
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
    assert (record.status, record.state) == (SessionStatus.COMPLETED, SessionState.COMPLETED)
    assert (record.target_found, record.target_position, record.results_count) == (True, 3, 10)
    assert record.observation_id == 77
    assert record.planned_dwell_seconds == 0.5
    assert 0.45 <= record.actual_dwell_seconds <= 0.8
    assert record.browser_info == {"version": "fake-1.0"}
    assert harness.engine.calls[0] == "start_session"
    assert harness.engine.calls[-2:] == ["end_session", "close"]


async def test_every_event_carries_the_state_it_happened_in(harness: Harness) -> None:
    await harness.run()
    by_type = {event.event_type: event.state for event in harness.sessions()[0].events}
    assert by_type[EventType.SEARCH_OBSERVED] is SessionState.ANALYZING_RESULTS
    assert by_type[EventType.TARGET_FOUND] is SessionState.TARGET_FOUND
    assert by_type[EventType.DWELL_STARTED] is SessionState.DWELLING
    assert by_type[EventType.SESSION_COMPLETED] is SessionState.COMPLETED


async def test_snapshot_keeps_the_observed_results_limited(harness: Harness) -> None:
    harness.rank.result_count = 40
    await harness.run(make_plan(requested_results=40))
    snapshot = harness.sessions()[0].settings_snapshot
    assert snapshot["observed_provider"] == "google-custom-search"
    assert snapshot["observed_result_count"] == 40
    assert len(snapshot["observed_results"]) == SNAPSHOT_RESULT_LIMIT
    assert snapshot["observed_results"][0] == {
        "position": 1,
        "title": "Other 1",
        "url": "https://site1.example/page",
    }
    assert snapshot["seed"] == 1234
    assert snapshot["mode"] == MODE_GOOGLE


# -- which page is opened ------------------------------------------------------------ #
async def test_the_page_google_listed_is_the_page_that_is_opened(harness: Harness) -> None:
    harness.rank.target_url = "https://shop.example/handmade-mugs"
    await harness.run()
    assert harness.engine.opened == ["https://shop.example/handmade-mugs"]
    assert EventType.SAFETY_BLOCKED not in harness.events()


async def test_a_listed_page_outside_the_allowlist_falls_back_to_the_approved_origin(
    harness: Harness,
) -> None:
    harness.rank.target_url = "https://other-shop.example/page"
    await harness.run()
    assert harness.engine.opened == [TARGET]  # the approved site, not the unapproved host
    blocked = next(
        e for e in harness.sessions()[0].events if e.event_type is EventType.SAFETY_BLOCKED
    )
    assert blocked.data == {"blocked_url": "https://other-shop.example/page", "used_url": TARGET}


# -- not listed ---------------------------------------------------------------------- #
async def test_target_not_listed_is_a_result_not_an_error(harness: Harness) -> None:
    harness.rank.position = None
    summary = await harness.run()
    assert (summary.target_not_found, summary.failed) == (1, 0)
    record = harness.sessions()[0]
    assert record.status is SessionStatus.TARGET_NOT_FOUND
    assert (record.target_found, record.target_position) == (False, None)
    assert record.state is SessionState.COMPLETED and record.error_type is None
    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.SEARCH_OBSERVED,
        EventType.TARGET_NOT_FOUND,
        EventType.SESSION_COMPLETED,
    ]
    # no page existed, so there was nothing to photograph either
    assert not [call for call in harness.engine.calls if call.startswith("screenshot:")]
    assert harness.engine.opened == []
    assert harness.observer.positions == [None]


# -- direct visits -------------------------------------------------------------------- #
async def test_direct_visit_opens_the_target_without_reading_the_ranking(harness: Harness) -> None:
    summary = await harness.run(direct_plan())
    assert (summary.completed, summary.failed) == (1, 0)
    assert harness.rank.calls == []  # the observation already happened, on the research page
    assert harness.engine.opened == [TARGET]
    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.SEARCH_OBSERVED,
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
    assert (record.target_found, record.target_position, record.results_count) == (True, 6, 10)
    assert harness.observer.positions == [6]


async def test_a_local_site_is_visited_without_any_rank_claim(harness: Harness) -> None:
    summary = await harness.run(
        make_plan(
            mode=MODE_DIRECT,
            provider="",
            observation_id=None,
            observed_position=None,
            observed_result_count=None,
        )
    )
    assert summary.completed == 1
    assert harness.rank.calls == []
    assert EventType.SEARCH_OBSERVED not in harness.events()
    record = harness.sessions()[0]
    assert (record.status, record.target_found, record.target_position) == (
        SessionStatus.COMPLETED,
        True,
        None,
    )
    # "no position" must not be shown as "not listed", which would be a claim about Google
    assert harness.observer.positions == []


# -- failures ------------------------------------------------------------------------- #
async def test_provider_failure_fails_the_session_without_a_browser(harness: Harness) -> None:
    harness.rank.error = GoogleQuotaError("Daily quota exceeded for the project.")
    summary = await harness.run(make_plan(iterations=2))
    assert (summary.failed, summary.completed) == (2, 0)
    record = harness.sessions()[0]
    assert record.error_type == "GoogleQuotaError"
    assert "quota" in record.error_message
    assert harness.engine.opened == [] and "start_session" not in harness.engine.calls
    assert harness.events()[-1] is EventType.SESSION_FAILED


async def test_failure_after_the_ranking_is_recorded_and_the_run_continues(
    harness: Harness,
) -> None:
    harness.engine.fail_at["open_url"] = NavigationError("the page did not load")
    summary = await harness.run(make_plan(iterations=3))
    assert (summary.failed, summary.completed, summary.total) == (3, 0, 3)
    assert len(harness.sessions()) == 3
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.FAILED, SessionState.FAILED)
    assert (record.error_type, record.error_message) == ("NavigationError", "the page did not load")
    assert record.target_found is True and record.target_position == 3  # the ranking still counts
    assert harness.events()[-3:] == [
        EventType.ERROR,
        EventType.SCREENSHOT_CAPTURED,
        EventType.SESSION_FAILED,
    ]
    assert harness.engine.calls.count("end_session") == 3
    assert len(harness.rank.calls) == 3  # every iteration asks Google again


async def test_recovery_after_a_failed_iteration(harness: Harness) -> None:
    attempts = {"n": 0}

    async def flaky() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NavigationError("first one fails")

    harness.engine.hooks["open_url"] = flaky
    summary = await harness.run(make_plan(iterations=2))
    assert (summary.failed, summary.completed) == (1, 1)
    assert [s.status for s in harness.sessions()] == [SessionStatus.FAILED, SessionStatus.COMPLETED]


async def test_unexpected_exception_does_not_crash_the_runner(harness: Harness) -> None:
    harness.rank.error = ZeroDivisionError("bug")
    summary = await harness.run(make_plan(iterations=2))
    assert summary.failed == 2
    record = harness.sessions()[0]
    assert record.error_type == "ZeroDivisionError" and "Unexpected error" in record.error_message


async def test_browser_launch_failure_aborts_the_remaining_iterations(harness: Harness) -> None:
    harness.engine.fail_at["start_session"] = BrowserLaunchError("Chromium is missing")
    summary = await harness.run(make_plan(iterations=5))
    assert (summary.failed, summary.total) == (1, 1)
    assert harness.engine.calls.count("start_session") == 1


async def test_unapproved_target_is_blocked_before_google_is_asked(harness: Harness) -> None:
    summary = await harness.run(make_plan(target_url="https://not-approved.example/"))
    assert summary.failed == 1
    assert harness.rank.calls == []  # no query is spent on a site the user may not automate
    assert not {"start_session", "open_url"} & set(harness.engine.calls)
    record = harness.sessions()[0]
    assert record.error_type == "SafetyViolationError"
    assert EventType.SAFETY_BLOCKED in harness.events()


async def test_a_public_search_engine_can_never_be_a_target(harness: Harness) -> None:
    summary = await harness.run(make_plan(target_url="https://www.google.com/"))
    assert summary.failed == 1 and "start_session" not in harness.engine.calls
    assert "public search engine" in harness.sessions()[0].error_message


async def test_approval_revoked_between_iterations_stops_the_next_one(database: Database) -> None:
    allowed = [AllowedOrigin("shop.example")]
    safety = DomainSafetyService(lambda: list(allowed))
    harness = Harness(database, safety)

    first = await harness.runner().run_iteration(make_plan(iterations=2), 1, "run")
    assert first.status is SessionStatus.COMPLETED
    allowed.clear()
    second = await harness.runner().run_iteration(make_plan(iterations=2), 2, "run")
    assert second.status is SessionStatus.FAILED and second.error_type == "SafetyViolationError"


# -- dwell, interactions and observers ------------------------------------------------- #
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


async def test_observer_follows_the_run(harness: Harness) -> None:
    await harness.run()
    obs = harness.observer
    assert obs.states[0] is SessionState.INITIALIZING and obs.states[-1] is SessionState.COMPLETED
    assert obs.positions == [3]
    assert obs.pages == [TARGET]
    assert obs.ticks[0] == (0.5, 0.5) and obs.ticks[-1][0] == 0
    assert [r.status for r in obs.finished] == [SessionStatus.COMPLETED]


async def test_a_broken_observer_cannot_break_the_run(harness: Harness) -> None:
    summary = await harness.runner(Broken()).run_plan(make_plan())
    assert summary.completed == 1


async def test_same_seed_gives_the_same_dwell_and_schedule(harness: Harness) -> None:
    plan = make_plan(
        min_dwell_seconds=0.2, max_dwell_seconds=0.9, scroll_enabled=True, max_scroll_actions=3
    )
    for _ in range(2):
        await harness.runner().run_iteration(plan, 1, "run")
    first, second = (s.settings_snapshot for s in harness.sessions())
    assert first["chosen_dwell_seconds"] == second["chosen_dwell_seconds"]
    assert first["scheduled_actions"] == second["scheduled_actions"]


async def test_snapshot_makes_the_session_reproducible(harness: Harness) -> None:
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


# -- stop and pause -------------------------------------------------------------------- #
async def test_stop_during_dwell_ends_the_session_gracefully(harness: Harness) -> None:
    async def stop_when_opened() -> None:
        asyncio.get_running_loop().call_later(0.3, harness.control.stop)

    harness.engine.hooks["open_url"] = stop_when_opened
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


async def test_stop_after_the_ranking_never_opens_a_page(harness: Harness) -> None:
    """The stop is honoured at the next safe boundary, which is before the browser starts."""
    harness.rank.on_answer = harness.control.stop
    summary = await harness.run(make_plan(min_dwell_seconds=20, max_dwell_seconds=20))
    assert summary.stopped == 1
    assert "open_url" not in harness.engine.calls


async def test_stop_before_start_runs_nothing(harness: Harness) -> None:
    harness.control.stop()
    summary = await harness.run(make_plan(iterations=3))
    assert summary.total == 0 and harness.sessions() == []
    assert harness.engine.calls == ["close"]


async def test_stop_during_the_delay_between_iterations(harness: Harness) -> None:
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
    assert 0.95 <= record.actual_dwell_seconds <= 1.4  # pause excluded
    assert wall >= 1.9  # but it really did wait
    events = harness.events()
    assert events.count(EventType.PAUSED) == 1 and events.count(EventType.RESUMED) == 1
    assert events.index(EventType.PAUSED) < events.index(EventType.RESUMED)


async def test_stop_while_paused(harness: Harness) -> None:
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


async def test_revoked_approval_during_dwell_fails_the_session_at_once(database: Database) -> None:
    allowed = [AllowedOrigin("shop.example")]
    safety = DomainSafetyService(lambda: list(allowed))
    harness = Harness(database, safety)

    async def revoke() -> None:
        asyncio.get_running_loop().call_later(0.3, allowed.clear)

    harness.engine.hooks["open_url"] = revoke
    started = time.monotonic()
    summary = await harness.run(make_plan(min_dwell_seconds=30, max_dwell_seconds=30))
    assert time.monotonic() - started < 3
    assert summary.failed == 1
    record = harness.sessions()[0]
    assert record.error_type == "SafetyViolationError" and record.state is SessionState.FAILED
    assert (
        EventType.SAFETY_BLOCKED in harness.events()
        and EventType.DWELL_COMPLETED not in harness.events()
    )


# -- several iterations --------------------------------------------------------------- #
async def test_iterations_are_numbered_and_share_a_run_id(harness: Harness) -> None:
    summary = await harness.runner().run_plan(replace(make_plan(iterations=3)), run_id="run-42")
    assert summary.completed == 3
    sessions = harness.sessions()
    assert [s.iteration for s in sessions] == [1, 2, 3]
    assert {s.total_iterations for s in sessions} == {3}
    assert {s.run_id for s in sessions} == {"run-42"}
    assert (
        harness.engine.calls.count("start_session") == 3
        and harness.engine.calls.count("close") == 1
    )


async def test_a_missing_rank_observer_is_reported_as_a_configuration_problem(
    harness: Harness,
) -> None:
    """No provider means no answer, and an invented position would be worse than none."""
    summary = await harness.runner_without_provider().run_plan(make_plan())
    assert summary.failed == 1
    record = harness.sessions()[0]
    assert record.error_type == "InvalidConfigurationError"
    assert "search provider" in record.error_message
    assert harness.engine.opened == [] and harness.rank.calls == []
