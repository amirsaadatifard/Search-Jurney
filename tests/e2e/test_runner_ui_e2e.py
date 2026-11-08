"""End-to-end through the GUI: real Chromium, driven by clicking the actual buttons."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.automation.models import RunSummary
from src.database.repositories import SessionRepository
from src.domain.enums import EventType, SessionState, SessionStatus
from src.services.first_run_service import DEMO_SCENARIO_NAME
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.live_view_page import LiveViewPage
from src.ui.pages.runner_page import RunnerPage
from src.ui.run_controller import RunPhase
from src.ui.theme import ThemeManager, ThemeMode
from tests.e2e.conftest import configure

pytestmark = pytest.mark.e2e
RUN_TIMEOUT_MS = 90_000


@pytest.fixture
def window(qtbot: QtBot, app: AppContainer) -> Iterator[MainWindow]:
    qapp = cast(QApplication, QApplication.instance())
    main_window = MainWindow(app, ThemeManager(qapp, ThemeMode.LIGHT))
    main_window.show()
    yield main_window
    if main_window.runs.is_active:
        main_window.runs.stop()
        app.runs.join(15)
        qtbot.waitUntil(lambda: not main_window.runs.is_active, timeout=15_000)
    main_window.close()
    main_window.deleteLater()


def stored_sessions(app: AppContainer) -> list:  # type: ignore[type-arg]
    with app.database.session_scope() as session:
        records = list(SessionRepository(session).search(with_details=True))
    return sorted(records, key=lambda record: record.id)


def test_a_run_started_from_the_runner_page_is_watchable_and_recorded(
    qtbot: QtBot, window: MainWindow, app: AppContainer
) -> None:
    configure(app, min_dwell_seconds=3, max_dwell_seconds=3, max_scroll_actions=4)
    window.show_page(PageKey.RUNNER)
    runner = cast(RunnerPage, window.page(PageKey.RUNNER))
    live = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))
    assert runner.controls.start_button.isEnabled()

    with qtbot.waitSignal(window.runs.finished, timeout=RUN_TIMEOUT_MS) as finished:
        runner.controls.start_button.click()
        assert window.current_page_key() is PageKey.LIVE_VIEW  # opens the live view itself

        # While it runs: live data reaches the widgets.
        qtbot.waitUntil(lambda: window.runs.state is SessionState.DWELLING, timeout=30_000)
        qtbot.waitUntil(lambda: live.preview.has_frame, timeout=15_000)
        assert live.fields["scenario"].text() == DEMO_SCENARIO_NAME
        assert live.fields["url"].text().startswith(app.servers.target.url)
        assert live.fields["position"].text() == "#4"
        assert live.status_badge.text() == "Running"
        qtbot.waitUntil(lambda: live.countdown_value.text() not in {"—", "3.0 s"}, timeout=10_000)
        assert 0 < live.countdown.value() < 1000

    summary = cast(RunSummary, finished.args[0])
    assert (summary.completed, summary.failed) == (1, 0)

    assert window.runs.phase is RunPhase.IDLE and window.runs.progress() == 1.0
    assert live.status_badge.text() == "Finished"
    assert runner.counters["completed"].text() == "1 completed"
    assert runner.controls.start_button.isEnabled() and not runner.controls.stop_button.isEnabled()
    shown = "\n".join(live.timeline.plain_lines())
    for expected in ("SEARCH_OBSERVED", "TARGET_FOUND", "DWELL_COMPLETED", "SESSION_COMPLETED"):
        assert expected in shown
    assert shown == "\n".join(runner.timeline.plain_lines())  # both pages show the same timeline

    (record,) = stored_sessions(app)
    assert record.status is SessionStatus.COMPLETED and record.target_position == 4


def test_stop_button_ends_the_session_and_the_window_stays_responsive(
    qtbot: QtBot, window: MainWindow, app: AppContainer
) -> None:
    configure(app, iterations=3, min_dwell_seconds=60, max_dwell_seconds=60)
    window.show_page(PageKey.RUNNER)
    runner = cast(RunnerPage, window.page(PageKey.RUNNER))
    live = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))

    with qtbot.waitSignal(window.runs.finished, timeout=RUN_TIMEOUT_MS):
        runner.controls.start_button.click()
        qtbot.waitUntil(lambda: window.runs.state is SessionState.DWELLING, timeout=30_000)
        window.show_page(PageKey.SCENARIOS)  # the UI keeps working during a run
        window.show_page(PageKey.LIVE_VIEW)
        live.controls.stop_button.click()
        assert live.status_badge.text() == "Stopping…"

    (record,) = stored_sessions(app)  # remaining iterations never started
    assert (record.status, record.state) == (SessionStatus.STOPPED, SessionState.STOPPED)
    assert window.runs.summary.stopped == 1
    assert (
        "stopped" in window.statusBar().currentMessage().lower()
        or window.runs.phase is RunPhase.IDLE
    )


def test_pause_and_resume_buttons(qtbot: QtBot, window: MainWindow, app: AppContainer) -> None:
    configure(app, min_dwell_seconds=3, max_dwell_seconds=3, max_scroll_actions=0)
    window.show_page(PageKey.RUNNER)
    runner = cast(RunnerPage, window.page(PageKey.RUNNER))
    live = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))

    with qtbot.waitSignal(window.runs.finished, timeout=RUN_TIMEOUT_MS):
        runner.controls.start_button.click()
        qtbot.waitUntil(lambda: window.runs.state is SessionState.DWELLING, timeout=30_000)
        live.controls.pause_button.click()
        assert live.status_badge.text() == "Paused" and live.controls.resume_button.isEnabled()
        qtbot.waitUntil(lambda: window.runs.state is SessionState.PAUSED, timeout=5_000)
        frozen = live.countdown_value.text()
        qtbot.wait(800)
        assert live.countdown_value.text() == frozen  # the countdown holds still
        live.controls.resume_button.click()

    (record,) = stored_sessions(app)
    assert record.status is SessionStatus.COMPLETED
    types = [event.event_type for event in record.events]
    assert EventType.PAUSED in types and EventType.RESUMED in types
