"""SessionRunner behaviour, verified against a fake engine (no browser)."""

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
from src.automation.interaction_service import ScheduledAction
from src.automation.models import (
    BrowserSettings,
    InteractionOutcome,
    ProxySettings,
    ResultItem,
    RunSummary,
    SessionPlan,
    SessionResult,
)
from src.automation.observer import RunObserver
from src.automation.recorder import SessionRecorder
from src.automation.session_runner import SessionRunner
from src.automation.target_actions import TargetActions
from src.database.repositories import SessionRepository
from src.database.session import Database
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus
from src.domain.exceptions import BrowserLaunchError, NavigationError
from src.services.safety_service import AllowedOrigin, DomainSafetyService

SEARCH = "http://127.0.0.1:8765/"
TARGET = "http://127.0.0.1:8766/"
Hook = Callable[[], Awaitable[None]]


class FakeEngine(AutomationEngine):
    """Scriptable engine: records calls, can fail at a step or run a hook there."""

    def __init__(self, target_position: int | None = 3) -> None:
        self.target_position = target_position
        self.calls: list[str] = []
        self.fail_at: dict[str, Exception] = {}
        self.hooks: dict[str, Hook] = {}
        self._url = ""

    @property
    def current_url(self) -> str:
        return self._url

    async def _step(self, name: str) -> None:
        self.calls.append(name)
        if name in self.hooks:
            await self.hooks[name]()
        if name in self.fail_at:
            raise self.fail_at[name]

    async def start_session(self, plan: SessionPlan) -> dict[str, Any]:
        await self._step("start_session")
        return {"version": "fake-1.0"}

    async def navigate(self, url: str) -> str:
        await self._step("navigate")
        self._url = url
        return url

    async def open_url(self, url: str) -> str:
        await self._step("open_url")
        self._url = url
        return url

    async def fill_search(self, keyword: str) -> None:
        await self._step("fill_search")

    async def submit_search(self) -> None:
        await self._step("submit_search")
        self._url = f"{SEARCH}search?q=x"

    async def get_results(self) -> list[ResultItem]:
        await self._step("get_results")
        results = [
            ResultItem(n, f"Other {n}", f"https://site{n}.example/page") for n in range(1, 11)
        ]
        if self.target_position is not None:
            results[self.target_position - 1] = ResultItem(self.target_position, "T", TARGET)
        return results

    def find_target(self, results: Sequence[ResultItem], target_url: str) -> ResultItem | None:
        return TargetActions.find_target(results, target_url)

    async def open_target(self, item: ResultItem) -> str:
        await self._step("open_target")
        self._url = TARGET
        return TARGET

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
        "search_url": SEARCH,
        "target_url": TARGET,
        "target_domain": "127.0.0.1",
        "simulated_position": 3,
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
    }
    return SessionPlan(**(values | overrides))


@pytest.fixture
def safety() -> DomainSafetyService:
    return DomainSafetyService(
        allowlist_provider=lambda: [AllowedOrigin("127.0.0.1", 8766)],
        internal_origins=[AllowedOrigin("127.0.0.1", 8765)],
    )


class Harness:
    def __init__(self, database: Database, safety: DomainSafetyService) -> None:
        self.database = database
        self.engine = FakeEngine()
        self.control = RunControl()
        self.observer = Recording()
        self.safety = safety
        self.configured: list[SessionPlan] = []
        self.seed = 1234

    def runner(self, observer: RunObserver | None = None) -> SessionRunner:
        return SessionRunner(
            self.engine,
            SessionRecorder(self.database),
            self.safety,
            self.control,
            observer if observer is not None else self.observer,
            configure_search=self.configured.append,
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


# -- success -------------------------------------------------------------------- #
async def test_successful_session_records_the_full_timeline(harness: Harness) -> None:
    summary = await harness.run()
    assert (summary.completed, summary.failed, summary.total) == (1, 0, 1)

    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.BROWSER_STARTED,
        EventType.SEARCH_PAGE_OPENED,
        EventType.KEYWORD_ENTERED,
        EventType.SEARCH_SUBMITTED,
        EventType.RESULTS_RECEIVED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.TARGET_FOUND,
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
    assert record.planned_dwell_seconds == 0.5
    assert 0.45 <= record.actual_dwell_seconds <= 0.8
    assert record.finished_at is not None and record.duration_seconds >= 0.5
    assert record.browser_info == {"version": "fake-1.0"}
    assert record.keyword == "handmade ceramic mugs" and record.scenario_name == "Unit scenario"
    assert harness.engine.calls[0] == "start_session"
    assert harness.engine.calls[-2:] == ["end_session", "close"]


async def test_every_event_carries_the_state_it_happened_in(harness: Harness) -> None:
    await harness.run()
    by_type = {event.event_type: event.state for event in harness.sessions()[0].events}
    assert by_type[EventType.SEARCH_PAGE_OPENED] is SessionState.OPENING_SEARCH
    assert by_type[EventType.TARGET_FOUND] is SessionState.TARGET_FOUND
    assert by_type[EventType.DWELL_STARTED] is SessionState.DWELLING
    assert by_type[EventType.SESSION_COMPLETED] is SessionState.COMPLETED
    timestamps = [event.timestamp for event in harness.sessions()[0].events]
    assert timestamps == sorted(timestamps)


async def test_snapshot_makes_the_session_reproducible(harness: Harness) -> None:
    plan = make_plan(
        scroll_enabled=True,
        max_scroll_actions=4,
        min_dwell_seconds=1,
        max_dwell_seconds=1,
        proxy=ProxySettings("lab", "HTTP", "10.0.0.1", 3128, "user", "s3cret"),
    )
    harness.control.stop()  # only the first session row matters here
    runner = harness.runner()
    await runner.run_iteration(plan, 1, "run-x")
    snapshot = harness.sessions()[0].settings_snapshot
    assert snapshot["seed"] == 1234
    assert snapshot["keyword"] == "handmade ceramic mugs"
    assert snapshot["max_scroll_actions"] == 4
    assert snapshot["proxy"]["host"] == "10.0.0.1"
    assert "password" not in snapshot["proxy"]
    assert "s3cret" not in str(snapshot)


async def test_same_seed_gives_the_same_dwell_and_schedule(harness: Harness) -> None:
    plan = make_plan(
        min_dwell_seconds=0.2, max_dwell_seconds=0.9, scroll_enabled=True, max_scroll_actions=3
    )
    for _ in range(2):
        await harness.runner().run_iteration(plan, 1, "run")
    first, second = (s.settings_snapshot for s in harness.sessions())
    assert first["chosen_dwell_seconds"] == second["chosen_dwell_seconds"]
    assert first["scheduled_actions"] == second["scheduled_actions"]


async def test_scenario_search_environment_is_configured_before_the_browser(
    harness: Harness,
) -> None:
    await harness.run()
    assert len(harness.configured) == 1 and harness.configured[0].target_url == TARGET


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
    assert set(interacting) == {SessionState.INTERACTING}  # recorded where it happened
    assert SessionState.INTERACTING in harness.observer.states


async def test_scroll_disabled_means_no_interactions(harness: Harness) -> None:
    await harness.run(make_plan(scroll_enabled=False, max_scroll_actions=9))
    assert "interaction" not in harness.engine.calls


async def test_observer_follows_the_run(harness: Harness) -> None:
    await harness.run()
    obs = harness.observer
    assert obs.states[0] is SessionState.INITIALIZING and obs.states[-1] is SessionState.COMPLETED
    assert obs.positions == [3]
    assert obs.pages[0] == SEARCH and obs.pages[-1] == TARGET
    assert obs.ticks[0] == (0.5, 0.5) and obs.ticks[-1][0] == 0
    remaining = [tick[0] for tick in obs.ticks]
    assert remaining == sorted(remaining, reverse=True)
    assert [r.status for r in obs.finished] == [SessionStatus.COMPLETED]


async def test_a_broken_observer_cannot_break_the_run(harness: Harness) -> None:
    summary = await harness.runner(Broken()).run_plan(make_plan())
    assert summary.completed == 1


# -- target not found ------------------------------------------------------------- #
async def test_target_not_listed_is_a_result_not_an_error(harness: Harness) -> None:
    harness.engine.target_position = None
    summary = await harness.run()
    assert (summary.target_not_found, summary.failed) == (1, 0)
    record = harness.sessions()[0]
    assert record.status is SessionStatus.TARGET_NOT_FOUND
    assert (record.target_found, record.target_position) == (False, None)
    assert record.state is SessionState.COMPLETED and record.error_type is None
    events = harness.events()
    assert EventType.TARGET_NOT_FOUND in events and EventType.DWELL_STARTED not in events
    assert "open_target" not in harness.engine.calls
    assert harness.observer.positions == [None]


# -- failures --------------------------------------------------------------------- #
async def test_failure_is_recorded_and_the_run_continues(harness: Harness) -> None:
    harness.engine.fail_at["fill_search"] = NavigationError("search box missing")
    summary = await harness.run(make_plan(iterations=3))
    assert (summary.failed, summary.completed, summary.total) == (3, 0, 3)
    assert len(harness.sessions()) == 3
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.FAILED, SessionState.FAILED)
    assert (record.error_type, record.error_message) == ("NavigationError", "search box missing")
    events = harness.events()
    assert events[-3:] == [EventType.ERROR, EventType.SCREENSHOT_CAPTURED, EventType.SESSION_FAILED]
    assert harness.engine.calls.count("end_session") == 3


async def test_recovery_after_a_failed_iteration(harness: Harness) -> None:
    attempts = {"n": 0}

    async def flaky() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NavigationError("first one fails")

    harness.engine.hooks["navigate"] = flaky
    summary = await harness.run(make_plan(iterations=2))
    assert (summary.failed, summary.completed) == (1, 1)
    assert [s.status for s in harness.sessions()] == [SessionStatus.FAILED, SessionStatus.COMPLETED]


async def test_unexpected_exception_does_not_crash_the_runner(harness: Harness) -> None:
    harness.engine.fail_at["get_results"] = ZeroDivisionError("bug")
    summary = await harness.run(make_plan(iterations=2))
    assert summary.failed == 2
    record = harness.sessions()[0]
    assert record.error_type == "ZeroDivisionError" and "Unexpected error" in record.error_message


async def test_browser_launch_failure_aborts_the_remaining_iterations(harness: Harness) -> None:
    harness.engine.fail_at["start_session"] = BrowserLaunchError("Chromium is missing")
    summary = await harness.run(make_plan(iterations=5))
    assert (summary.failed, summary.total) == (1, 1)
    assert harness.engine.calls.count("start_session") == 1


async def test_unapproved_target_is_blocked_before_any_browser_work(harness: Harness) -> None:
    summary = await harness.run(make_plan(target_url="https://example.com/"))
    assert summary.failed == 1
    assert not {"start_session", "navigate"} & set(harness.engine.calls)  # no browser work
    record = harness.sessions()[0]
    assert record.error_type == "SafetyViolationError"
    assert EventType.SAFETY_BLOCKED in harness.events()
    assert harness.configured == []


async def test_public_search_engine_cannot_be_used_as_search_page(harness: Harness) -> None:
    summary = await harness.run(make_plan(search_url="https://www.google.com/"))
    assert summary.failed == 1 and "start_session" not in harness.engine.calls
    assert "public search engine" in harness.sessions()[0].error_message


async def test_approval_revoked_between_iterations_stops_the_next_one(
    database: Database,
) -> None:
    allowed = [AllowedOrigin("127.0.0.1", 8766)]
    safety = DomainSafetyService(lambda: list(allowed), [AllowedOrigin("127.0.0.1", 8765)])
    harness = Harness(database, safety)

    first = await harness.runner().run_iteration(make_plan(iterations=2), 1, "run")
    assert first.status is SessionStatus.COMPLETED
    allowed.clear()
    second = await harness.runner().run_iteration(make_plan(iterations=2), 2, "run")
    assert second.status is SessionStatus.FAILED and second.error_type == "SafetyViolationError"


# -- stop and pause ------------------------------------------------------------------ #
async def test_stop_during_dwell_ends_the_session_gracefully(harness: Harness) -> None:
    async def stop_when_opened() -> None:
        asyncio.get_running_loop().call_later(0.3, harness.control.stop)

    harness.engine.hooks["open_target"] = stop_when_opened
    plan = make_plan(min_dwell_seconds=20, max_dwell_seconds=20, iterations=3)
    started = time.monotonic()
    summary = await harness.run(plan)
    assert time.monotonic() - started < 3  # did not wait out the 20 s dwell
    assert (summary.stopped, summary.total) == (1, 1)  # remaining iterations never start
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.STOPPED, SessionState.STOPPED)
    assert record.finished_at is not None and record.error_type is None
    assert harness.events()[-1] is EventType.SESSION_STOPPED
    assert harness.engine.calls[-2:] == ["end_session", "close"]


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
    paused_at = next(e for e in harness.sessions()[0].events if e.event_type is EventType.PAUSED)
    assert paused_at.state is SessionState.PAUSED
    assert SessionState.PAUSED in harness.observer.states
    resumed_index = harness.observer.states.index(SessionState.PAUSED) + 1
    assert harness.observer.states[resumed_index] is SessionState.DWELLING


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


# -- several iterations ------------------------------------------------------------- #
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


async def test_revoked_approval_during_dwell_fails_the_session_at_once(database: Database) -> None:
    allowed = [AllowedOrigin("127.0.0.1", 8766)]
    safety = DomainSafetyService(lambda: list(allowed), [AllowedOrigin("127.0.0.1", 8765)])
    harness = Harness(database, safety)

    async def revoke() -> None:
        asyncio.get_running_loop().call_later(0.3, allowed.clear)

    harness.engine.hooks["open_target"] = revoke
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


# -- direct visit (Google Research mode) ------------------------------------------------------ #
def direct_plan(**overrides: Any) -> SessionPlan:
    values: dict[str, Any] = {
        "search_url": "",
        "mode": "direct",
        "observation_id": None,
        "observed_position": 6,
        "observed_result_count": 10,
        "provider": "google-custom-search",
        "keyword": "handmade mugs",
        "simulated_position": None,
    }
    return make_plan(**(values | overrides))


async def test_direct_visit_opens_the_target_without_any_search_step(harness: Harness) -> None:
    summary = await harness.run(direct_plan())
    assert (summary.completed, summary.failed) == (1, 0)
    calls = harness.engine.calls
    assert "open_url" in calls and not {
        "navigate",
        "fill_search",
        "submit_search",
        "get_results",
    } & set(calls)
    assert harness.configured == []  # the local simulator is never involved

    assert harness.events() == [
        EventType.SESSION_STARTED,
        EventType.BROWSER_STARTED,
        EventType.SEARCH_OBSERVED,
        EventType.TARGET_FOUND,
        EventType.TARGET_OPENED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.DWELL_STARTED,
        EventType.DWELL_COMPLETED,
        EventType.SCREENSHOT_CAPTURED,
        EventType.SESSION_COMPLETED,
    ]
    record = harness.sessions()[0]
    assert (record.status, record.state) == (SessionStatus.COMPLETED, SessionState.COMPLETED)
    assert (record.target_found, record.target_position, record.results_count) == (True, 6, 10)
    observed = next(e for e in record.events if e.event_type is EventType.SEARCH_OBSERVED)
    assert "google-custom-search" in observed.message and "position 6" in observed.message
    assert harness.engine.current_url == TARGET and harness.observer.positions == [6]


async def test_direct_visit_still_enforces_the_allowlist(harness: Harness) -> None:
    summary = await harness.run(direct_plan(target_url="https://not-approved.example/"))
    assert (
        summary.failed == 1
        and "open_url" not in harness.engine.calls
        and "start_session" not in harness.engine.calls
    )
    assert harness.sessions()[0].error_type == "SafetyViolationError"


async def test_direct_visit_failure_is_recorded_like_any_other(harness: Harness) -> None:
    harness.engine.fail_at["open_url"] = NavigationError("site unreachable")
    summary = await harness.run(direct_plan())
    assert summary.failed == 1
    record = harness.sessions()[0]
    assert (record.error_message, record.state) == ("site unreachable", SessionState.FAILED)
    assert (
        EventType.SEARCH_OBSERVED in harness.events()
        and EventType.DWELL_STARTED not in harness.events()
    )
