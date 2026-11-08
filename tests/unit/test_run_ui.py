"""Run controller, Runner page, Live View page and run controls (no browser)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from typing import Any, cast

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.automation.models import RunSummary, SessionResult
from src.automation.observer import RunObserver
from src.domain.enums import SessionState, SessionStatus
from src.domain.exceptions import AutomationError
from src.services.first_run_service import DEMO_SCENARIO_NAME
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.live_view_page import LiveViewPage
from src.ui.pages.runner_page import RunnerPage
from src.ui.run_controller import RunPhase, event_tone, session_progress
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.log_viewer import LogLine, LogViewer
from tests.unit.test_session_runner import make_plan


class FakeRuns:
    """Stands in for AutomationRunService: records commands, lets tests drive the observer."""

    def __init__(self) -> None:
        self.observer: RunObserver | None = None
        self.started_with: list[list[int]] = []
        self.overrides: list[int | None] = []
        self.commands: list[str] = []
        self.fail_start: Exception | None = None

    @property
    def is_running(self) -> bool:
        return self.observer is not None

    def start(
        self,
        ids: Sequence[int],
        observer: RunObserver | None = None,
        iterations_override: int | None = None,
    ) -> None:
        if self.fail_start is not None:
            raise self.fail_start
        self.started_with.append(list(ids))
        self.overrides.append(iterations_override)
        self.observer = observer

    def pause(self) -> None:
        self.commands.append("pause")

    def resume(self) -> None:
        self.commands.append("resume")

    def stop(self) -> None:
        self.commands.append("stop")

    def shutdown(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> bool:
        return True

    def drive(self, action: Callable[[RunObserver], None]) -> None:
        """Run ``action`` against the observer on a separate thread, like the real worker."""
        assert self.observer is not None
        worker = threading.Thread(target=action, args=(self.observer,))
        worker.start()
        worker.join()


def result(status: SessionStatus = SessionStatus.COMPLETED, iteration: int = 1) -> SessionResult:
    state = SessionState.COMPLETED if status is not SessionStatus.FAILED else SessionState.FAILED
    return SessionResult(7, iteration, status, state, target_found=True, target_position=3)


def jpeg_bytes(width: int = 320, height: int = 200) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG")
    return bytes(data.data())


@pytest.fixture
def fake_runs(container: AppContainer) -> FakeRuns:
    runs = FakeRuns()
    container.runs = runs  # type: ignore[assignment]
    return runs


@pytest.fixture
def window(container: AppContainer, fake_runs: FakeRuns) -> Iterator[MainWindow]:
    """Main window. Closed here (not by qtbot) so a still-"active" fake run cannot
    open the quit-confirmation dialog during teardown and block the test."""
    app = cast(QApplication, QApplication.instance())
    main_window = MainWindow(container, ThemeManager(app, ThemeMode.LIGHT))
    main_window.show()
    yield main_window
    main_window.runs.phase = RunPhase.IDLE
    main_window.close()
    main_window.deleteLater()


def begin(window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot, iterations: int = 2) -> None:
    """Start a run through the controller and feed it the first events."""
    window.runs.start([1])
    plan = make_plan(iterations=iterations)

    def first(observer: RunObserver) -> None:
        observer.on_run_started([plan])
        observer.on_scenario_started(plan, 1, 1)
        observer.on_session_started(7, 1, iterations)

    fake_runs.drive(first)
    qtbot.waitUntil(lambda: window.runs.total_sessions == iterations)


# -- pure helpers ------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "order",
    [
        # a journey that reads its ranking from Google first
        [
            SessionState.INITIALIZING,
            SessionState.SEARCHING,
            SessionState.ANALYZING_RESULTS,
            SessionState.TARGET_FOUND,
            SessionState.STARTING_BROWSER,
            SessionState.OPENING_TARGET,
            SessionState.DWELLING,
            SessionState.CAPTURING_RESULT,
            SessionState.COMPLETED,
        ],
        # a visit of an already observed (or unlistable) site
        [
            SessionState.INITIALIZING,
            SessionState.TARGET_FOUND,
            SessionState.STARTING_BROWSER,
            SessionState.OPENING_TARGET,
            SessionState.DWELLING,
            SessionState.CAPTURING_RESULT,
            SessionState.COMPLETED,
        ],
        # and a session that stops at the results
        [
            SessionState.INITIALIZING,
            SessionState.SEARCHING,
            SessionState.ANALYZING_RESULTS,
            SessionState.TARGET_NOT_FOUND,
            SessionState.CAPTURING_RESULT,
            SessionState.COMPLETED,
        ],
    ],
)
def test_session_progress_increases_along_the_journey(order: list[SessionState]) -> None:
    values = [session_progress(state) for state in order]
    assert values == sorted(values) and values[0] > 0 and values[-1] == 1.0
    assert session_progress(None) == 0 and session_progress(SessionState.IDLE) == 0
    assert session_progress(SessionState.DWELLING, 0.0) < session_progress(
        SessionState.DWELLING, 1.0
    )
    assert session_progress(SessionState.DWELLING, 9) == session_progress(SessionState.DWELLING, 1)
    assert session_progress(SessionState.FAILED) == session_progress(SessionState.STOPPED) == 1.0


def test_event_tones() -> None:
    assert event_tone("ERROR") == event_tone("SAFETY_BLOCKED") == "danger"
    assert event_tone("TARGET_FOUND") == "success" and event_tone("PAUSED") == "warning"
    assert event_tone("SCROLL") == "neutral"


# -- controller --------------------------------------------------------------------- #
def test_controller_follows_a_run_driven_from_another_thread(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    runs = window.runs
    assert not runs.is_active and not runs.has_run and runs.progress() == 0
    begin(window, fake_runs, qtbot)

    assert runs.phase is RunPhase.RUNNING and fake_runs.started_with == [[1]]
    assert (runs.scenario_name, runs.keyword) == ("Unit scenario", "handmade ceramic mugs")
    assert (runs.iteration, runs.total_iterations, runs.total_sessions) == (1, 2, 2)
    assert (runs.browser_name, runs.proxy_name) == ("Test", "Direct connection")

    def middle(observer: RunObserver) -> None:
        observer.on_state_changed(SessionState.SEARCHING, "Submitting the search")
        observer.on_page_changed("http://127.0.0.1:8765/search?q=x")
        observer.on_event("SEARCH_SUBMITTED", "submitted", {})
        observer.on_target_position(4)
        observer.on_state_changed(SessionState.DWELLING, "Dwelling for 10 s")
        observer.on_dwell_tick(6.0, 10.0)
        observer.on_preview(jpeg_bytes())

    previews: list[object] = []
    runs.preview.connect(previews.append)
    fake_runs.drive(middle)
    qtbot.waitUntil(lambda: bool(previews))

    assert runs.state is SessionState.DWELLING and runs.step == "Dwelling for 10 s"
    assert runs.url.endswith("search?q=x") and runs.target_position == 4
    assert runs.dwell_remaining == 6.0 and runs.dwell_total == 10.0
    assert runs.dwell_fraction() == pytest.approx(0.4)
    assert [entry.event_type for entry in runs.entries] == ["SEARCH_SUBMITTED"]
    assert 0.25 < runs.progress() < 0.5  # inside session 1 of 2, past the halfway of its steps
    assert isinstance(previews[0], bytes)

    finished: list[RunSummary] = []
    runs.finished.connect(finished.append)
    fake_runs.drive(
        lambda o: (
            o.on_session_finished(result()),
            o.on_session_started(8, 2, 2),
            o.on_session_finished(result(SessionStatus.TARGET_NOT_FOUND, 2)),
            o.on_run_finished(RunSummary(completed=1, target_not_found=1)),
        )
    )
    qtbot.waitUntil(lambda: bool(finished))
    assert runs.phase is RunPhase.IDLE and runs.has_run and runs.progress() == 1.0
    assert (runs.summary.completed, runs.summary.target_not_found) == (1, 1)
    assert finished[0].total == 2


def test_pause_resume_stop_commands(window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot) -> None:
    runs = window.runs
    assert not (runs.can_pause or runs.can_resume or runs.can_stop)
    begin(window, fake_runs, qtbot)
    assert runs.can_pause and runs.can_stop and not runs.can_resume

    runs.pause()
    assert runs.phase is RunPhase.PAUSED and runs.can_resume and not runs.can_pause
    runs.resume()
    assert runs.phase is RunPhase.RUNNING
    runs.stop()
    assert runs.phase is RunPhase.STOPPING and not runs.can_stop
    assert fake_runs.commands == ["pause", "resume", "stop"]


def test_session_clock_freezes_while_paused(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    runs = window.runs
    begin(window, fake_runs, qtbot)
    fake_runs.drive(lambda o: o.on_state_changed(SessionState.DWELLING, "Dwelling"))
    qtbot.waitUntil(lambda: runs.state is SessionState.DWELLING)
    qtbot.wait(150)
    fake_runs.drive(lambda o: o.on_state_changed(SessionState.PAUSED, "Paused"))
    qtbot.waitUntil(lambda: runs.state is SessionState.PAUSED)
    frozen = runs.session_elapsed()
    qtbot.wait(300)
    assert runs.session_elapsed() == frozen > 0.1
    fake_runs.drive(lambda o: o.on_state_changed(SessionState.DWELLING, "Resumed"))
    qtbot.waitUntil(lambda: runs.state is SessionState.DWELLING)
    qtbot.wait(200)
    assert runs.session_elapsed() > frozen


def test_start_validation_and_failure_restore_the_idle_state(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    with pytest.raises(AutomationError, match="at least one"):
        window.runs.start([])
    fake_runs.fail_start = AutomationError("port in use")
    with pytest.raises(AutomationError, match="port in use"):
        window.runs.start([1])
    assert window.runs.phase is RunPhase.IDLE
    fake_runs.fail_start = None
    window.runs.start([1])
    with pytest.raises(AutomationError, match="already in progress"):
        window.runs.start([1])


# -- runner page ------------------------------------------------------------------- #
def test_runner_page_lists_enabled_scenarios_and_labels_start(window: MainWindow) -> None:
    window.show_page(PageKey.RUNNER)
    page = cast(RunnerPage, window.page(PageKey.RUNNER))
    assert page.table.row_count() == 1
    assert page.table.cell_text(0, 0) == DEMO_SCENARIO_NAME
    assert page.controls.start_button.text() == "Start all enabled"
    assert page.controls.start_button.isEnabled() and not page.controls.pause_button.isEnabled()
    page.table.selectRow(0)
    assert page.controls.start_button.text() == "Start 1 selected"
    assert page.selected_scenario_ids() == [page.table.object_at_position(0).id]
    page.table.clearSelection()
    assert len(page.selected_scenario_ids()) == 1  # nothing selected means "all enabled"


def test_runner_page_without_enabled_scenarios(window: MainWindow, container: AppContainer) -> None:
    container.scenarios.set_enabled(container.scenarios.search()[0].id, False)
    window.show_page(PageKey.RUNNER)
    page = cast(RunnerPage, window.page(PageKey.RUNNER))
    assert page.table.row_count() == 0 and not page.table.isVisible()
    assert page.empty_state.isVisible() and not page.controls.start_button.isEnabled()


def test_start_button_starts_the_run_and_opens_the_live_view(
    window: MainWindow, fake_runs: FakeRuns
) -> None:
    window.show_page(PageKey.RUNNER)
    page = cast(RunnerPage, window.page(PageKey.RUNNER))
    page.controls.start_button.click()
    assert fake_runs.started_with == [[page.table.object_at_position(0).id]]
    assert window.current_page_key() is PageKey.LIVE_VIEW
    assert window.runs.phase is RunPhase.RUNNING
    assert not page.controls.start_button.isEnabled()  # cannot start twice
    assert page.controls.stop_button.isEnabled() and page.controls.pause_button.isEnabled()


def test_start_error_is_shown_as_a_toast(window: MainWindow, fake_runs: FakeRuns) -> None:
    fake_runs.fail_start = AutomationError("Search Simulator could not start.")
    window.show_page(PageKey.RUNNER)
    cast(RunnerPage, window.page(PageKey.RUNNER)).controls.start_button.click()
    assert window.current_page_key() is PageKey.RUNNER
    assert window.notifier.toast.tone() == "danger"
    assert "could not start" in window.notifier.toast.message()


def test_runner_page_shows_progress_counts_and_timeline(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    window.show_page(PageKey.RUNNER)
    page = cast(RunnerPage, window.page(PageKey.RUNNER))
    page.start_run()
    plan = make_plan(iterations=2)
    fake_runs.drive(
        lambda o: (
            o.on_run_started([plan]),
            o.on_scenario_started(plan, 1, 1),
            o.on_session_started(7, 1, 2),
            o.on_event("TARGET_FOUND", "position=3", {}),
            o.on_session_finished(result()),
        )
    )
    qtbot.waitUntil(lambda: page.timeline.line_count() == 1)
    assert page.timeline.plain_lines()[0].endswith("TARGET_FOUND  position=3")
    assert page.counters["completed"].text() == "1 completed"
    assert page.progress.value() == 500 and page.progress_text.text() == "Session 2 of 2"

    fake_runs.drive(lambda o: o.on_run_finished(RunSummary(completed=1)))
    qtbot.waitUntil(lambda: not window.runs.is_active)
    assert page.status_line.text().startswith("Finished: 1 completed")
    assert window.notifier.toast.tone() == "success"
    assert page.controls.start_button.isEnabled()


# -- live view --------------------------------------------------------------------- #
def test_live_view_shows_an_empty_state_until_a_run_starts(window: MainWindow) -> None:
    window.show_page(PageKey.LIVE_VIEW)
    page = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))
    assert page._stack.currentIndex() == 0 and page.empty_state.isVisible()
    assert not page.controls.start_button.isVisible()  # starting happens in the Runner


def test_live_view_renders_every_required_field(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    window.show_page(PageKey.LIVE_VIEW)
    page = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))
    begin(window, fake_runs, qtbot)
    fake_runs.drive(
        lambda o: (
            o.on_state_changed(SessionState.DWELLING, "Dwelling for 12 s"),
            o.on_page_changed("http://127.0.0.1:8766/"),
            o.on_target_position(4),
            o.on_dwell_tick(7.5, 12.0),
            o.on_preview(jpeg_bytes()),
        )
    )
    qtbot.waitUntil(lambda: page.preview.has_frame)

    fields = {key: label.text() for key, label in page.fields.items()}
    assert fields["scenario"] == "Unit scenario" and fields["keyword"] == "handmade ceramic mugs"
    assert fields["iteration"] == "1 of 2"
    assert fields["step"] == "Dwelling for 12 s" and fields["url"] == "http://127.0.0.1:8766/"
    assert fields["position"] == "#4"
    assert fields["browser"] == "Test" and fields["proxy"] == "Direct connection"
    assert page.countdown_value.text() == "7.5 s" and page.countdown.value() == 375
    assert page.status_badge.text() == "Running"
    assert page._stack.currentIndex() == 1
    assert page.controls.pause_button.isEnabled() and page.controls.stop_button.isEnabled()

    page.controls.pause_button.click()
    assert page.status_badge.text() == "Paused" and page.controls.resume_button.isEnabled()
    page.controls.stop_button.click()
    assert page.status_badge.text() == "Stopping…"
    assert fake_runs.commands == ["pause", "stop"]


def test_target_not_listed_is_displayed(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    window.show_page(PageKey.LIVE_VIEW)
    page = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))
    begin(window, fake_runs, qtbot)
    fake_runs.drive(lambda o: o.on_target_position(None))
    qtbot.waitUntil(lambda: window.runs.target_resolved)
    assert page.fields["position"].text() == "Not listed"


def test_new_run_clears_the_previous_timeline_and_preview(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot
) -> None:
    page = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))
    begin(window, fake_runs, qtbot)
    fake_runs.drive(lambda o: (o.on_event("SCROLL", "down", {}), o.on_preview(jpeg_bytes())))
    qtbot.waitUntil(lambda: page.timeline.line_count() == 1 and page.preview.has_frame)
    fake_runs.drive(lambda o: o.on_run_finished(RunSummary(completed=1)))
    qtbot.waitUntil(lambda: not window.runs.is_active)
    window.runs.start([1])
    assert page.timeline.line_count() == 0 and not page.preview.has_frame


# -- widgets and window ----------------------------------------------------------------- #
def test_log_viewer_keeps_lines_across_theme_changes(qtbot: QtBot) -> None:
    app = cast(QApplication, QApplication.instance())
    theme = ThemeManager(app, ThemeMode.LIGHT)
    viewer = LogViewer(theme)
    qtbot.addWidget(viewer)
    viewer.add_line(LogLine("10:00:00", "TARGET_FOUND", "<b>position=4</b>", "success"))
    viewer.add_line(LogLine("10:00:01", "ERROR", "boom", "danger"))
    assert viewer.line_count() == 2
    assert "<b>" in viewer.toPlainText()  # message text is shown, not interpreted as HTML
    theme.set_mode(ThemeMode.DARK)
    assert viewer.toPlainText().count("\n") == 1 and "boom" in viewer.toPlainText()
    viewer.clear_lines()
    assert viewer.line_count() == 0 and viewer.toPlainText() == ""


def test_closing_during_a_run_asks_for_confirmation(
    window: MainWindow, fake_runs: FakeRuns, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin(window, fake_runs, qtbot)
    asked: list[str] = []
    answers = iter([False, True])

    def ask(_cls: Any, _parent: Any, title: str, *_args: Any, **_kwargs: Any) -> bool:
        asked.append(title)
        return next(answers)

    monkeypatch.setattr(ConfirmDialog, "ask", classmethod(ask))
    assert not window.close()  # declined: the window stays open
    assert window.isVisible() and len(asked) == 1
    assert window.close()  # confirmed
    assert not window.isVisible()
