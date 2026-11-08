"""End-to-end: the one-click demo, started with the real dashboard button."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.automation.models import RunSummary
from src.database.repositories import SessionRepository
from src.domain.enums import SessionStatus
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.dashboard_page import DashboardPage
from src.ui.pages.live_view_page import LiveViewPage
from src.ui.theme import ThemeManager, ThemeMode
from tests.e2e.conftest import configure

pytestmark = pytest.mark.e2e


@pytest.fixture
def window(qtbot: QtBot, app: AppContainer) -> Iterator[MainWindow]:
    main_window = MainWindow(
        app, ThemeManager(cast(QApplication, QApplication.instance()), ThemeMode.LIGHT)
    )
    main_window.show()
    yield main_window
    if main_window.runs.is_active:
        main_window.runs.stop()
        app.runs.join(15)
        qtbot.waitUntil(lambda: not main_window.runs.is_active, timeout=15_000)
    main_window.close()
    main_window.deleteLater()


def test_demo_button_runs_exactly_one_safe_cycle(
    qtbot: QtBot, window: MainWindow, app: AppContainer
) -> None:
    # The demo scenario normally runs twice for 8-15 s each; a user may have changed anything else.
    configure(app, min_dwell_seconds=2, max_dwell_seconds=2, iterations=2, enabled=False)
    dashboard = cast(DashboardPage, window.page(PageKey.DASHBOARD))
    live = cast(LiveViewPage, window.page(PageKey.LIVE_VIEW))

    with qtbot.waitSignal(window.runs.finished, timeout=90_000) as finished:
        dashboard.demo_button.click()
        assert window.current_page_key() is PageKey.LIVE_VIEW
        assert not dashboard.demo_button.isEnabled()

    summary = cast(RunSummary, finished.args[0])
    assert (summary.completed, summary.total) == (1, 1)  # one cycle, although the scenario says 2
    with app.database.session_scope() as session:
        (record,) = SessionRepository(session).search()
    assert record.status is SessionStatus.COMPLETED and record.total_iterations == 1
    assert record.target_domain == "127.0.0.1" and record.target_position == 4
    assert live.status_badge.text() == "Finished"
    assert (
        dashboard.demo_button.isEnabled() and dashboard.cards["Total sessions"].value_text() == "1"
    )
