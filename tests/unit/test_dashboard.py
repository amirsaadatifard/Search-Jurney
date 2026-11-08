"""Dashboard statistics, chart helpers and the dashboard page."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import cast

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.database.repositories import SessionRepository
from src.domain.enums import SessionStatus
from src.services.dashboard_service import DashboardService, daily_points
from src.services.timeutil import local_day_start_utc
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.dashboard_page import KPI_LABELS, DashboardPage
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.charts import (
    BarChart,
    LineChart,
    OutcomeBar,
    OutcomeChart,
    format_value,
    nice_ticks,
)
from tests.factories import make_session

TODAY = date(2026, 3, 12)


def at(day: date, hours: float = 0):  # type: ignore[no-untyped-def]
    return local_day_start_utc(day) + timedelta(hours=12 + hours)


def seed(container: AppContainer) -> None:
    with container.database.session_scope() as session:
        repo = SessionRepository(session)
        for hours, position, dwell in ((0, 2, 10.0), (1, 4, 20.0)):
            repo.add(
                make_session(at(TODAY, hours), target_position=position, actual_dwell_seconds=dwell)
            )
        repo.add(
            make_session(
                at(TODAY - timedelta(days=1)), target_position=6, actual_dwell_seconds=30.0
            )
        )
        repo.add(make_session(at(TODAY - timedelta(days=3)), SessionStatus.FAILED))
        repo.add(make_session(at(TODAY - timedelta(days=3), 1), SessionStatus.TARGET_NOT_FOUND))
        repo.add(
            make_session(
                at(TODAY - timedelta(days=40)), target_position=1, actual_dwell_seconds=5.0
            )
        )


@pytest.fixture
def window(container: AppContainer) -> Iterator[MainWindow]:
    app = cast(QApplication, QApplication.instance())
    main_window = MainWindow(container, ThemeManager(app, ThemeMode.LIGHT))
    main_window.show()
    yield main_window
    main_window.close()
    main_window.deleteLater()


# -- helpers ----------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("maximum", "integer", "expected"),
    [
        (0, False, [0.0, 1.0]),
        (7, False, [0.0, 2.0, 4.0, 6.0, 8.0]),
        (3, True, [0.0, 1.0, 2.0, 3.0]),
        (1, True, [0.0, 1.0]),
        (100, False, [0.0, 25.0, 50.0, 75.0, 100.0]),
        (0.8, False, [0.0, 0.2, 0.4, 0.6, 0.8]),
    ],
)
def test_nice_ticks(maximum: float, integer: bool, expected: list[float]) -> None:
    ticks = nice_ticks(maximum, integer=integer)
    assert ticks == pytest.approx(expected) and ticks[-1] >= maximum


def test_nice_ticks_always_cover_the_data() -> None:
    for maximum in (0.3, 1.7, 9, 13, 47, 120, 999, 10_000):
        ticks = nice_ticks(maximum)
        assert ticks[0] == 0 and ticks[-1] >= maximum and 2 <= len(ticks) <= 8


def test_format_value() -> None:
    assert (format_value(5), format_value(5.0, " s"), format_value(2.46)) == ("5", "5 s", "2.5")


def test_daily_points_cover_every_day_and_average_only_what_exists() -> None:
    with_data = [
        make_session(at(TODAY), target_position=2, actual_dwell_seconds=10.0),
        make_session(at(TODAY, 1), target_position=4, actual_dwell_seconds=20.0),
        make_session(at(TODAY - timedelta(days=2)), SessionStatus.FAILED),
    ]
    points = daily_points(with_data, TODAY, days=5)
    assert [p.day for p in points] == [TODAY - timedelta(days=n) for n in (4, 3, 2, 1, 0)]
    assert [p.sessions for p in points] == [0, 0, 1, 0, 2]
    assert (points[-1].average_position, points[-1].average_dwell_seconds) == (3.0, 15.0)
    assert points[2].average_position is None  # a failed session has no position: a gap, not a zero
    assert points[0].average_dwell_seconds is None


# -- service ----------------------------------------------------------------------------- #
def test_service_loads_every_number(container: AppContainer) -> None:
    seed(container)
    stats = DashboardService(container.database).load(days=14, today=TODAY)
    assert (stats.total_scenarios, stats.enabled_scenarios) == (1, 1)
    assert stats.total_sessions == 6 and (
        stats.successful,
        stats.failed,
        stats.target_not_found,
    ) == (4, 1, 1)
    assert stats.success_rate == pytest.approx(4 / 6)
    assert stats.sessions_today == 2
    assert stats.average_target_position == pytest.approx((2 + 4 + 6 + 1) / 4)
    assert stats.average_dwell_seconds == pytest.approx((10 + 20 + 30 + 5) / 4)
    assert (
        len(stats.daily) == 14 and sum(p.sessions for p in stats.daily) == 5
    )  # the 40-day-old one is outside
    assert stats.last_execution is not None and stats.last_scenario == "Demo Scenario"
    assert [status for status, _ in stats.outcomes()] == [
        SessionStatus.COMPLETED,
        SessionStatus.TARGET_NOT_FOUND,
        SessionStatus.FAILED,
        SessionStatus.STOPPED,
    ]


def test_service_with_no_sessions(container: AppContainer) -> None:
    stats = container.dashboard.load()
    assert stats.total_sessions == 0 and stats.success_rate is None and stats.last_execution is None
    assert all(p.sessions == 0 for p in stats.daily)


# -- charts -------------------------------------------------------------------------------- #
def test_bar_chart_geometry_tooltips_and_table_rows(qtbot: QtBot) -> None:
    app = cast(QApplication, QApplication.instance())
    chart = BarChart(ThemeManager(app, ThemeMode.LIGHT), integer=True)
    qtbot.addWidget(chart)
    chart.resize(500, 220)
    chart.set_data(["Mar 10", "Mar 11", "Mar 12"], [0, 4, 2])
    rect = chart.plot_rect()
    assert chart.index_at(rect.left() + 1) == 0 and chart.index_at(rect.right() - 1) == 2
    assert chart.index_at(rect.left() - 5) is None and chart.index_at(rect.right() + 5) is None
    assert chart.x_of(0) < chart.x_of(1) < chart.x_of(2)
    assert chart.tooltip_text(1) == "Mar 11\n4"
    assert chart.rows() == [("Mar 10", "0"), ("Mar 11", "4"), ("Mar 12", "2")]
    assert chart.has_data()
    chart.set_data(["a"], [0])
    assert not chart.has_data()  # all zeros reads as "no data yet"


def test_line_chart_keeps_gaps_and_units(qtbot: QtBot) -> None:
    app = cast(QApplication, QApplication.instance())
    chart = LineChart(ThemeManager(app, ThemeMode.LIGHT), unit=" s")
    qtbot.addWidget(chart)
    chart.resize(400, 200)
    chart.set_data(["a", "b", "c"], [10.0, None, 12.5])
    assert chart.rows() == [("a", "10 s"), ("b", "—"), ("c", "12.5 s")]
    assert chart.tooltip_text(1) == "b\nno data"
    chart.set_data(["a", "b"], [None, None])
    assert not chart.has_data()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_every_chart_paints_something(qtbot: QtBot, mode: ThemeMode) -> None:
    app = cast(QApplication, QApplication.instance())
    theme = ThemeManager(app, mode)
    bars, line = BarChart(theme, integer=True), LineChart(theme)
    outcomes = OutcomeChart(theme)
    for chart in (bars, line, outcomes):
        qtbot.addWidget(chart)
        chart.resize(480, 220)
    bars.set_data(["a", "b", "c", "d"], [1, 3, 2, 5])
    line.set_data(["a", "b", "c", "d"], [3.0, None, 2.0, 4.0])
    outcomes.set_bars(
        [OutcomeBar("Completed", 5, QColor("#0ca30c")), OutcomeBar("Failed", 2, QColor("#d03b3b"))]
    )
    for chart in (bars, line, outcomes):
        image = chart.grab().toImage()
        colors = {
            image.pixelColor(x, y).rgb()
            for x in range(0, image.width(), 7)
            for y in range(0, image.height(), 7)
        }
        assert len(colors) > 2, f"{type(chart).__name__} drew nothing"


def test_hover_updates_the_highlight(qtbot: QtBot) -> None:
    app = cast(QApplication, QApplication.instance())
    chart = LineChart(ThemeManager(app, ThemeMode.LIGHT))
    qtbot.addWidget(chart)
    chart.resize(400, 200)
    chart.show()
    chart.set_data(["a", "b", "c"], [1.0, 2.0, 3.0])
    qtbot.mouseMove(chart, QPoint(int(chart.x_of(1)), 80))
    qtbot.waitUntil(lambda: chart.hover_index == 1)
    chart.leaveEvent(None)
    assert chart.hover_index is None
    assert QPointF(0, 0) is not None and Qt.AlignmentFlag.AlignLeft is not None


def test_outcome_rows_show_counts_and_shares(qtbot: QtBot) -> None:
    app = cast(QApplication, QApplication.instance())
    chart = OutcomeChart(ThemeManager(app, ThemeMode.LIGHT))
    qtbot.addWidget(chart)
    assert chart.total() == 0
    chart.set_bars(
        [OutcomeBar("Completed", 3, QColor("#0ca30c")), OutcomeBar("Failed", 1, QColor("#d03b3b"))]
    )
    assert chart.rows() == [("Completed", "3  (75%)"), ("Failed", "1  (25%)")]


# -- page ------------------------------------------------------------------------------------- #
def test_dashboard_fills_cards_and_charts(window: MainWindow, container: AppContainer) -> None:
    today = date.today()
    with container.database.session_scope() as session:
        repo = SessionRepository(session)
        repo.add(make_session(at(today), target_position=2, actual_dwell_seconds=12.0))
        repo.add(make_session(at(today, 1), SessionStatus.FAILED))
    window.show_page(PageKey.DASHBOARD)
    page = cast(DashboardPage, window.page(PageKey.DASHBOARD))
    assert set(page.cards) == set(KPI_LABELS)
    values = {label: card.value_text() for label, card in page.cards.items()}
    assert values["Total scenarios"] == "1" and values["Enabled scenarios"] == "1"
    assert values["Total sessions"] == "2" and values["Failed sessions"] == "1"
    assert values["Success rate"] == "50 %" and values["Sessions today"] == "2"
    assert values["Average search position"] == "2.0" and values["Average dwell time"] == "12 s"
    assert values["Current running scenario"] == "—" and values["Last execution"] != "—"
    assert page.sessions_chart.values[-1] == 2 and page.position_chart.values[-1] == 2.0
    assert page.outcome_chart.total() == 2


def test_dashboard_empty_state_and_current_run(window: MainWindow) -> None:
    page = cast(DashboardPage, window.page(PageKey.DASHBOARD))
    assert page.cards["Total sessions"].value_text() == "0"
    assert (
        page.cards["Success rate"].value_text() == "—"
        and page.cards["Last execution"].value_text() == "—"
    )
    assert not page.sessions_chart.has_data() and page.outcome_chart.total() == 0

    runs = window.runs
    runs.scenario_name, runs.iteration, runs.total_iterations = "Demo scenario", 2, 5
    from src.ui.run_controller import RunPhase

    runs.phase = RunPhase.RUNNING
    runs.changed.emit()
    assert page.cards["Current running scenario"].value_text() == "Demo scenario"
    runs.phase = RunPhase.IDLE
    runs.changed.emit()
    assert page.cards["Current running scenario"].value_text() == "—"


def test_table_view_shows_the_same_numbers(window: MainWindow, container: AppContainer) -> None:
    with container.database.session_scope() as session:
        SessionRepository(session).add(
            make_session(at(date.today()), target_position=3, actual_dwell_seconds=9.0)
        )
    window.show_page(PageKey.DASHBOARD)
    page = cast(DashboardPage, window.page(PageKey.DASHBOARD))
    card = page.chart_cards["outcomes"]
    assert card.stack.currentIndex() == 0 and card.toggle.text() == "Table view"
    card.toggle.setChecked(True)
    assert card.stack.currentIndex() == 1 and card.toggle.text() == "Chart view"
    assert card.table.cell_text(0, 0) == "Completed" and card.table.cell_text(0, 1) == "1  (100%)"
    dwell = page.chart_cards["dwell"]
    dwell.toggle.setChecked(True)
    assert dwell.table.row_count() == 14 and dwell.table.cell_text(13, 1) == "9 s"
    assert dwell.table.cell_text(0, 1) == "—"
    card.toggle.setChecked(False)
    assert card.stack.currentIndex() == 0


def test_dashboard_refreshes_when_a_run_finishes(
    window: MainWindow, container: AppContainer
) -> None:
    from src.automation.models import RunSummary

    page = cast(DashboardPage, window.page(PageKey.DASHBOARD))
    assert page.cards["Total sessions"].value_text() == "0"
    with container.database.session_scope() as session:
        SessionRepository(session).add(make_session(at(date.today())))
    window.runs.finished.emit(RunSummary(completed=1))
    assert page.cards["Total sessions"].value_text() == "1"
