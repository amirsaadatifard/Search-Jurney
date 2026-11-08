"""End-to-end: real Chromium + the local simulator + the sample site + the database.

Every test drives the application the way the GUI does: through
``AutomationRunService`` on its worker thread. Nothing leaves 127.0.0.1.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from src.app.container import AppContainer
from src.automation.models import RunSummary, SessionResult
from src.automation.observer import RunObserver
from src.database.models import AutomationSession
from src.database.repositories import SessionRepository
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus
from tests.e2e.conftest import configure

pytestmark = pytest.mark.e2e
RUN_TIMEOUT = 90.0


class Probe(RunObserver):
    """Collects what the GUI would see; can run callbacks when a state is reached."""

    def __init__(self) -> None:
        self.states: list[SessionState] = []
        self.pages: list[str] = []
        self.ticks: list[float] = []
        self.finished: list[SessionResult] = []
        self.summary: RunSummary | None = None
        self.done = threading.Event()
        self.on_state: dict[SessionState, Callable[[], None]] = {}

    def on_state_changed(self, state: SessionState, message: str) -> None:
        self.states.append(state)
        if state in self.on_state:
            self.on_state.pop(state)()

    def on_page_changed(self, url: str) -> None:
        self.pages.append(url)

    def on_dwell_tick(self, remaining_seconds: float, planned_seconds: float) -> None:
        self.ticks.append(remaining_seconds)

    def on_session_finished(self, result: SessionResult) -> None:
        self.finished.append(result)

    def on_run_finished(self, summary: RunSummary) -> None:
        self.summary = summary
        self.done.set()


def run(container: AppContainer, probe: Probe | None = None) -> Probe:
    """Run the demo scenario to completion and return what the observer saw."""
    probe = probe or Probe()
    scenario_id = container.scenarios.search()[0].id
    container.runs.start([scenario_id], probe)
    assert probe.done.wait(RUN_TIMEOUT), "the run did not finish in time"
    assert container.runs.join(10)
    return probe


def sessions(container: AppContainer) -> list[AutomationSession]:
    with container.database.session_scope() as session:
        records = list(SessionRepository(session).search(with_details=True))
    return sorted(records, key=lambda record: record.id)


def event_types(record: AutomationSession) -> list[EventType]:
    return [event.event_type for event in record.events]


# -- the main journey ------------------------------------------------------------ #
def test_full_search_journey(app: AppContainer) -> None:
    probe = run(app)

    assert probe.summary is not None and (probe.summary.completed, probe.summary.total) == (1, 1)
    (record,) = sessions(app)
    assert (record.status, record.state) == (SessionStatus.COMPLETED, SessionState.COMPLETED)
    assert (record.target_found, record.target_position, record.results_count) == (True, 4, 10)
    assert record.planned_dwell_seconds is not None and 1 <= record.planned_dwell_seconds <= 2
    assert record.actual_dwell_seconds is not None
    assert (
        record.planned_dwell_seconds - 0.3
        <= record.actual_dwell_seconds
        <= record.planned_dwell_seconds + 3
    )
    assert record.error_type is None and record.duration_seconds and record.duration_seconds > 1
    assert "chromium" in record.browser_info["browser"] and record.browser_info["headless"] is True

    types = event_types(record)
    ordered = [
        EventType.SESSION_STARTED,
        EventType.BROWSER_STARTED,
        EventType.SEARCH_PAGE_OPENED,
        EventType.KEYWORD_ENTERED,
        EventType.SEARCH_SUBMITTED,
        EventType.RESULTS_RECEIVED,
        EventType.TARGET_FOUND,
        EventType.TARGET_OPENED,
        EventType.DWELL_STARTED,
        EventType.DWELL_COMPLETED,
        EventType.SESSION_COMPLETED,
    ]
    positions = [types.index(item) for item in ordered]
    assert positions == sorted(positions)
    target_event = next(e for e in record.events if e.event_type is EventType.TARGET_FOUND)
    assert target_event.message == "position=4" and target_event.data == {"position": 4}

    # What the live view would have shown.
    assert (
        probe.states[0] is SessionState.INITIALIZING and probe.states[-1] is SessionState.COMPLETED
    )
    assert SessionState.DWELLING in probe.states
    assert probe.pages[0].startswith(app.servers.search.url)
    assert probe.pages[-1].startswith(app.servers.target.url)
    assert probe.ticks[0] == record.planned_dwell_seconds and probe.ticks[-1] == 0
    assert not app.runs.is_running


def test_screenshots_are_saved_and_recorded(app: AppContainer) -> None:
    run(app)
    (record,) = sessions(app)
    by_kind = {shot.kind: shot for shot in record.screenshots}
    assert set(by_kind) == {
        ScreenshotKind.SEARCH_RESULTS,
        ScreenshotKind.TARGET_OPENED,
        ScreenshotKind.SESSION_END,
    }
    for kind, shot in by_kind.items():
        path = Path(shot.file_path)
        assert path.is_file() and path.stat().st_size == shot.size_bytes > 1000
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert path.parent.parent == app.paths.screenshots_dir  # data/screenshots/<date>/
        assert path.name == (
            f"session_{record.id}_keyword_handmade-ceramic-mugs_{kind.value.lower()}.png"
        )
        assert (shot.width, shot.height) == (1280, 800)
    assert by_kind[ScreenshotKind.SEARCH_RESULTS].page_url.startswith(app.servers.search.url)
    assert by_kind[ScreenshotKind.TARGET_OPENED].page_url.startswith(app.servers.target.url)
    assert event_types(record).count(EventType.SCREENSHOT_CAPTURED) == 3


def test_screenshot_policy_can_disable_captures(app: AppContainer) -> None:
    from src.services.settings_service import SettingKey

    app.settings.set(SettingKey.SCREENSHOT_POLICY, {"search_results": False, "session_end": False})
    run(app)
    (record,) = sessions(app)
    assert [shot.kind for shot in record.screenshots] == [ScreenshotKind.TARGET_OPENED]


def test_dwell_includes_scrolling_and_stays_on_the_approved_site(app: AppContainer) -> None:
    configure(app, min_dwell_seconds=5, max_dwell_seconds=5, max_scroll_actions=8)
    probe = run(app)
    (record,) = sessions(app)
    interactions = [
        e for e in record.events if e.event_type in {EventType.SCROLL, EventType.PAGE_NAVIGATION}
    ]
    assert len(interactions) >= 2
    scrolls = [e for e in interactions if e.event_type is EventType.SCROLL]
    assert scrolls and all(e.data["direction"] in {"down", "up"} for e in scrolls)
    assert record.scroll_actions == len(scrolls)
    assert SessionState.INTERACTING in probe.states
    target = app.servers.target.url
    assert all(url.startswith((app.servers.search.url, target)) for url in probe.pages)
    for event in interactions:
        if event.event_type is EventType.PAGE_NAVIGATION:
            assert event.data["visited"].startswith(target)
    assert record.status is SessionStatus.COMPLETED


# -- result positions ---------------------------------------------------------------- #
@pytest.mark.parametrize("position", [1, 7, 10])
def test_target_is_found_at_each_simulated_position(app: AppContainer, position: int) -> None:
    configure(app, simulated_position=position, min_dwell_seconds=1, max_dwell_seconds=1)
    run(app)
    (record,) = sessions(app)
    assert record.status is SessionStatus.COMPLETED and record.target_position == position


def test_target_not_listed_is_reported_without_dwelling(app: AppContainer) -> None:
    configure(app, simulated_position=None)
    probe = run(app)
    (record,) = sessions(app)
    assert record.status is SessionStatus.TARGET_NOT_FOUND
    assert (record.target_found, record.target_position, record.state) == (
        False,
        None,
        SessionState.COMPLETED,
    )
    types = event_types(record)
    assert EventType.TARGET_NOT_FOUND in types
    assert not {EventType.TARGET_OPENED, EventType.DWELL_STARTED} & set(types)
    assert {s.kind for s in record.screenshots} == {
        ScreenshotKind.SEARCH_RESULTS,
        ScreenshotKind.SESSION_END,
    }
    assert probe.summary is not None and probe.summary.target_not_found == 1


# -- iterations and browser state --------------------------------------------------------- #
@pytest.mark.parametrize("incognito", [True, False])
def test_several_iterations_run_back_to_back(app: AppContainer, incognito: bool) -> None:
    configure(
        app,
        iterations=3,
        incognito=incognito,
        delay_between_iterations=1,
        min_dwell_seconds=1,
        max_dwell_seconds=1,
        max_scroll_actions=0,
    )
    probe = run(app)
    records = sessions(app)
    assert [r.status for r in records] == [SessionStatus.COMPLETED] * 3
    assert [r.iteration for r in records] == [1, 2, 3]
    assert len({r.run_id for r in records}) == 1
    assert probe.summary is not None and probe.summary.completed == 3
    names = {Path(s.file_path).name for r in records for s in r.screenshots}
    assert len(names) == 9  # distinct file per session and kind


# -- safety --------------------------------------------------------------------------------- #
def test_redirect_to_a_non_approved_site_is_blocked(app: AppContainer) -> None:
    site = app.target_sites.create(
        {
            "display_name": "Redirecting page",
            "base_url": f"{app.servers.target.url}/test/redirect-external",
            "approved_for_automation": True,
        }
    )
    configure(app, target_site_id=site.id)
    probe = run(app)

    (record,) = sessions(app)
    assert record.status is SessionStatus.FAILED and record.state is SessionState.FAILED
    assert record.error_type == "SafetyViolationError"
    assert "outside-the-allowlist.example" in (record.error_message or "")
    types = event_types(record)
    assert EventType.SAFETY_BLOCKED in types and EventType.SESSION_FAILED in types
    assert EventType.DWELL_STARTED not in types
    assert all("outside-the-allowlist" not in url for url in probe.pages)
    assert probe.summary is not None and probe.summary.failed == 1


def test_revoked_approval_ends_the_session_immediately(app: AppContainer) -> None:
    probe = Probe()
    site = app.target_sites.search()[0]
    # Revoke the approval the moment the target page is open, i.e. mid-session.
    probe.on_state[SessionState.DWELLING] = lambda: app.target_sites.set_approved(site.id, False)
    configure(app, min_dwell_seconds=30, max_dwell_seconds=30, max_scroll_actions=8)
    run(app, probe)
    (record,) = sessions(app)
    assert record.status is SessionStatus.FAILED
    assert record.error_type == "SafetyViolationError"
    assert (
        record.duration_seconds is not None and record.duration_seconds < 15
    )  # not the full dwell
    assert EventType.SAFETY_BLOCKED in event_types(record)


def test_missing_search_simulator_fails_cleanly(app: AppContainer) -> None:
    app.servers.search.stop()
    app.servers.search.start = lambda: None  # type: ignore[method-assign]
    run(app)
    (record,) = sessions(app)
    assert record.status is SessionStatus.FAILED
    assert record.error_type in {"NavigationError", "AutomationTimeoutError"}
    assert record.state is SessionState.FAILED


# -- stop and pause ------------------------------------------------------------------------------ #
def test_stop_during_dwell(app: AppContainer) -> None:
    configure(app, iterations=3, min_dwell_seconds=60, max_dwell_seconds=60)
    probe = Probe()
    probe.on_state[SessionState.DWELLING] = app.runs.stop
    run(app, probe)
    records = sessions(app)
    assert len(records) == 1  # later iterations never started
    assert (records[0].status, records[0].state) == (SessionStatus.STOPPED, SessionState.STOPPED)
    assert event_types(records[0])[-1] is EventType.SESSION_STOPPED
    assert records[0].duration_seconds is not None and records[0].duration_seconds < 20
    assert probe.summary is not None and probe.summary.stopped == 1
    assert not app.runs.is_running


def test_pause_and_resume_a_running_session(app: AppContainer) -> None:
    configure(app, min_dwell_seconds=3, max_dwell_seconds=3, max_scroll_actions=0)
    probe = Probe()
    paused = threading.Event()

    def pause_now() -> None:
        app.runs.pause()
        paused.set()

    probe.on_state[SessionState.DWELLING] = pause_now
    app.runs.start([app.scenarios.search()[0].id], probe)
    assert paused.wait(RUN_TIMEOUT)
    threading.Event().wait(1.0)
    assert app.runs.is_paused and not probe.done.is_set()
    app.runs.resume()
    assert probe.done.wait(RUN_TIMEOUT) and app.runs.join(10)

    (record,) = sessions(app)
    assert record.status is SessionStatus.COMPLETED
    types = event_types(record)
    assert types.count(EventType.PAUSED) == 1 and types.count(EventType.RESUMED) == 1
    assert SessionState.PAUSED in probe.states
    assert record.actual_dwell_seconds is not None and record.actual_dwell_seconds < 3.8


def test_only_one_run_at_a_time(app: AppContainer) -> None:
    from src.domain.exceptions import AutomationError

    configure(app, min_dwell_seconds=5, max_dwell_seconds=5)
    probe = Probe()
    started = threading.Event()
    probe.on_state[SessionState.DWELLING] = started.set
    app.runs.start([app.scenarios.search()[0].id], probe)
    assert started.wait(RUN_TIMEOUT)
    with pytest.raises(AutomationError, match="already in progress"):
        app.runs.start([app.scenarios.search()[0].id])
    app.runs.stop()
    assert probe.done.wait(RUN_TIMEOUT) and app.runs.join(10)


def test_application_shutdown_stops_a_running_run(app: AppContainer) -> None:
    configure(app, min_dwell_seconds=60, max_dwell_seconds=60)
    probe = Probe()
    started = threading.Event()
    probe.on_state[SessionState.DWELLING] = started.set
    app.runs.start([app.scenarios.search()[0].id], probe)
    assert started.wait(RUN_TIMEOUT)
    app.runs.shutdown()
    assert not app.runs.is_running
    assert sessions(app)[0].status is SessionStatus.STOPPED


# -- the network fence ------------------------------------------------------------------------ #
def test_fence_isolates_other_services_on_this_computer(app: AppContainer) -> None:
    """A live service on another local port must be unreachable from the test browser."""
    import asyncio
    import http.server

    from src.automation.browser_manager import BrowserManager

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OTHER LOCAL SERVICE")

        def log_message(self, *args: object) -> None:
            pass

    other = http.server.HTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    app.servers.start_all()
    plan = app.runs.build_plans([app.scenarios.search()[0].id])[0]

    async def scenario() -> dict[str, str]:
        manager = BrowserManager(app.safety)
        await manager.launch(plan.browser, plan)
        session = await manager.open_session(plan)
        outcome: dict[str, str] = {"fence": str(manager.fence_active)}
        try:
            for label, url in {
                "approved": plan.target_url,
                "other_port": f"http://127.0.0.1:{other.server_port}/",
                "external_ip": "http://93.184.216.34/",
            }.items():
                try:
                    await session.page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    outcome[label] = "reached: " + (await session.page.inner_text("body"))[:30]
                except Exception as exc:
                    outcome[label] = "blocked: " + str(exc).splitlines()[0][-40:]
        finally:
            await manager.close()
        return outcome

    try:
        outcome = asyncio.run(scenario())
    finally:
        other.shutdown()
    assert outcome["fence"] == "True"
    assert outcome["approved"].startswith("reached")
    assert "OTHER LOCAL SERVICE" not in outcome["other_port"]
    assert outcome["other_port"].startswith("blocked") and outcome["external_ip"].startswith(
        "blocked"
    )
