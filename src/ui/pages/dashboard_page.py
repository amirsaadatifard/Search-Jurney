"""Dashboard: headline KPIs and charts."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGridLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.domain.enums import SessionStatus
from src.services.dashboard_service import DashboardStats
from src.ui.formatting import format_datetime, format_seconds
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.run_controller import RunController
from src.ui.session_status import STATUS_LABELS
from src.ui.widgets.chart_card import ChartCard
from src.ui.widgets.charts import BarChart, LineChart, OutcomeBar, OutcomeChart
from src.ui.widgets.metric_card import MetricCard

KPI_COLUMNS: Final = 4
KPI_LABELS: Final[tuple[str, ...]] = (
    "Total scenarios",
    "Enabled scenarios",
    "Total sessions",
    "Successful sessions",
    "Failed sessions",
    "Success rate",
    "Average dwell time",
    "Average search position",
    "Sessions today",
    "Last execution",
    "Current running scenario",
    "Target not found",
)
# Fixed status colours of the reference palette; labels and counts repeat the meaning.
OUTCOME_COLORS: Final[dict[SessionStatus, str]] = {
    SessionStatus.COMPLETED: "#0ca30c",
    SessionStatus.TARGET_NOT_FOUND: "#fab219",
    SessionStatus.FAILED: "#d03b3b",
    SessionStatus.STOPPED: "#898781",
}


class DashboardPage(BasePage):
    """Overview of scenarios and session outcomes."""

    def __init__(self, context: PageContext) -> None:
        super().__init__(context, "Dashboard", "Overview of test scenarios and automation results.")
        self._service = context.container.dashboard
        self._runs: RunController = context.runs
        theme = context.theme

        self.demo_button = QPushButton("Run demo")
        self.demo_button.setProperty("variant", "primary")
        self.demo_button.setToolTip(
            "Starts the local test servers and runs one safe search journey you can watch live."
        )
        self.demo_button.clicked.connect(context.start_demo)
        self.header.add_action(self.demo_button)

        content = QWidget()
        content.setObjectName("PageBody")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(18)

        self.cards = {label: MetricCard(label) for label in KPI_LABELS}
        grid = QGridLayout()
        grid.setSpacing(14)
        for index, card in enumerate(self.cards.values()):
            grid.addWidget(card, index // KPI_COLUMNS, index % KPI_COLUMNS)
        column.addLayout(grid)

        self.sessions_chart = BarChart(theme, integer=True)
        self.outcome_chart = OutcomeChart(theme)
        self.position_chart = LineChart(theme)
        self.dwell_chart = LineChart(theme, unit=" s")
        self.chart_cards = {
            "sessions": ChartCard(
                theme,
                "Sessions over time",
                "Sessions started per day, last 14 days.",
                self.sessions_chart,
                "Sessions",
            ),
            "outcomes": ChartCard(
                theme,
                "Success vs. failures",
                "How finished sessions ended.",
                self.outcome_chart,
                "Sessions (share)",
            ),
            "position": ChartCard(
                theme,
                "Average target position",
                "Lower is better: position of the target in the results, per day.",
                self.position_chart,
                "Position",
            ),
            "dwell": ChartCard(
                theme,
                "Average dwell time",
                "Seconds spent on the target page, per day.",
                self.dwell_chart,
                "Seconds",
            ),
        }
        charts = QGridLayout()
        charts.setSpacing(14)
        for index, chart_card in enumerate(self.chart_cards.values()):
            charts.addWidget(chart_card, index // 2, index % 2)
        column.addLayout(charts, 1)

        # Scrollable, so the charts keep their full height in a small window.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.body.addWidget(scroll, 1)

        self._runs.changed.connect(self._update_current_run)
        self._runs.finished.connect(lambda _summary: self.refresh())
        self.refresh()

    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Reload all statistics from the database."""
        self.apply(self._service.load())

    def apply(self, stats: DashboardStats) -> None:
        """Render ``stats`` into the cards and charts."""

        def pct(value: float | None) -> str | None:
            return None if value is None else f"{value * 100:.0f} %"

        values: dict[str, str | None] = {
            "Total scenarios": str(stats.total_scenarios),
            "Enabled scenarios": str(stats.enabled_scenarios),
            "Total sessions": str(stats.total_sessions),
            "Successful sessions": str(stats.successful),
            "Failed sessions": str(stats.failed),
            "Success rate": pct(stats.success_rate),
            "Average dwell time": (
                None
                if stats.average_dwell_seconds is None
                else format_seconds(stats.average_dwell_seconds)
            ),
            "Average search position": (
                None
                if stats.average_target_position is None
                else f"{stats.average_target_position:.1f}"
            ),
            "Sessions today": str(stats.sessions_today),
            "Last execution": (
                None if stats.last_execution is None else format_datetime(stats.last_execution)
            ),
            "Target not found": str(stats.target_not_found),
        }
        for label, value in values.items():
            self.cards[label].set_value(value)
        self.cards["Last execution"].set_value(values["Last execution"], stats.last_scenario)
        self._update_current_run()

        labels = [point.day.strftime("%b %d") for point in stats.daily]
        self.sessions_chart.set_data(labels, [float(p.sessions) for p in stats.daily])
        self.position_chart.set_data(labels, [p.average_position for p in stats.daily])
        self.dwell_chart.set_data(labels, [p.average_dwell_seconds for p in stats.daily])
        self.outcome_chart.set_bars(
            [
                OutcomeBar(STATUS_LABELS[status], count, QColor(OUTCOME_COLORS[status]))
                for status, count in stats.outcomes()
            ]
        )
        for chart_card in self.chart_cards.values():
            if chart_card.toggle.isChecked():
                chart_card.refresh_table()

    def _update_current_run(self) -> None:
        runs = self._runs
        self.demo_button.setEnabled(not runs.is_active)
        card = self.cards["Current running scenario"]
        if runs.is_active and runs.scenario_name:
            card.set_value(
                runs.scenario_name, f"Iteration {runs.iteration}/{runs.total_iterations}"
            )
        else:
            card.set_value(None, "No run in progress")
