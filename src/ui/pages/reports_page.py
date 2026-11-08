"""Reports: filter sessions, see the metrics, export to CSV / Excel / JSON."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.domain.enums import SessionStatus
from src.reports import export_csv, export_excel, export_json
from src.services.report_service import ReportData, ReportFilter
from src.ui.formatting import PLACEHOLDER
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.session_status import STATUS_LABELS, STATUS_TONES
from src.ui.widgets.card import Card
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.form_dialog import make_combo
from src.ui.widgets.metric_card import MetricCard
from src.ui.workers import TaskRunner

Exporter = Callable[[ReportData, Path], Path]
EXPORTERS: Final[dict[str, tuple[Exporter, str, str]]] = {
    "csv": (export_csv, ".csv", "CSV files (*.csv)"),
    "excel": (export_excel, ".xlsx", "Excel workbooks (*.xlsx)"),
    "json": (export_json, ".json", "JSON files (*.json)"),
}
METRIC_LABELS: Final = (
    "Sessions",
    "Successful",
    "Failed",
    "Success rate",
    "Average dwell time",
    "Average target position",
    "Target not found",
    "Average session duration",
)
_ROW_HEIGHT = 96


@dataclass(frozen=True, slots=True)
class PreviewRow:
    id: int
    scenario: str
    keyword: str
    status: str
    started: str
    duration: str
    position: str
    dwell: str


def preview_rows(report: ReportData) -> list[PreviewRow]:
    """Turn the sessions table into display rows."""

    def number(value: Any, unit: str = "") -> str:
        return PLACEHOLDER if value is None or value != value else f"{value:.1f}{unit}"

    rows: list[PreviewRow] = []
    for record in report.sessions.to_dict(orient="records"):
        position = record["target_position"]
        started = record["started_at"]
        rows.append(
            PreviewRow(
                id=int(record["id"]),
                scenario=str(record["scenario"]),
                keyword=str(record["keyword"]),
                status=str(record["status"]),
                started=started.strftime("%Y-%m-%d %H:%M") if isinstance(started, datetime) else "",
                duration=number(record["duration_seconds"], " s"),
                position=(
                    PLACEHOLDER if position is None or position != position else f"#{int(position)}"
                ),
                dwell=number(record["actual_dwell_seconds"], " s"),
            )
        )
    return rows


def _status(data: object) -> SessionStatus | None:
    """Qt returns enum members stored in a combo box as plain strings; restore the enum."""
    return None if data is None else SessionStatus(str(data))


class _DateFilter(QWidget):
    """A date picker that can be switched off ("any date")."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.enabled_box = QCheckBox(caption)
        self.edit = QDateEdit(QDate.currentDate())
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd")
        self.edit.setEnabled(False)
        self.edit.setMinimumWidth(124)
        self.enabled_box.toggled.connect(self.edit.setEnabled)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.enabled_box)
        layout.addWidget(self.edit)

    def value(self) -> date | None:
        if not self.enabled_box.isChecked():
            return None
        chosen = self.edit.date()
        return date(chosen.year(), chosen.month(), chosen.day())


class ReportsPage(BasePage):
    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context, "Reports", "Filter session results, review the metrics and export them."
        )
        self._service = context.container.reports
        self._runner = TaskRunner(self)
        self.report: ReportData | None = None

        # filters ---------------------------------------------------------------------
        self.date_from = _DateFilter("From")
        self.date_to = _DateFilter("To")
        self.scenario_filter = make_combo([("Any scenario", None)])
        self.keyword_filter = make_combo([("Any keyword", "")])
        self.target_filter = make_combo([("Any target", "")])
        self.status_filter = make_combo(
            [("Any status", None)] + [(STATUS_LABELS[s], s) for s in SessionStatus]
        )
        for combo in (
            self.scenario_filter,
            self.keyword_filter,
            self.target_filter,
            self.status_filter,
        ):
            combo.setMinimumWidth(150)
        self.generate_button = QPushButton("Generate report")
        self.generate_button.setProperty("variant", "primary")
        self.generate_button.clicked.connect(self.generate)

        filters = QGridLayout()
        filters.setHorizontalSpacing(12)
        filters.setVerticalSpacing(10)
        first_row = (self.date_from, self.date_to, self.status_filter)
        second_row = (self.scenario_filter, self.keyword_filter, self.target_filter)
        for position, widget in enumerate(first_row):
            filters.addWidget(widget, 0, position)
        for position, widget in enumerate(second_row):
            filters.addWidget(widget, 1, position)
        filters.addWidget(self.generate_button, 1, 3, Qt.AlignmentFlag.AlignRight)
        filters.setColumnStretch(3, 1)
        filter_card = Card()
        filter_card.body.addLayout(filters)

        # metrics ---------------------------------------------------------------------
        self.cards = {label: MetricCard(label) for label in METRIC_LABELS}
        grid = QGridLayout()
        grid.setSpacing(14)
        for index, card in enumerate(self.cards.values()):
            card.setMinimumHeight(_ROW_HEIGHT)
            grid.addWidget(card, index // 4, index % 4)

        # preview + export ---------------------------------------------------------------
        self.table: DataTable[PreviewRow] = DataTable(
            context.theme,
            [
                Column("ID", lambda r: str(r.id), lambda r: r.id, width=60),
                Column("Scenario", lambda r: r.scenario, stretch=True),
                Column("Keyword", lambda r: r.keyword, width=190),
                Column(
                    "Status",
                    lambda r: STATUS_LABELS[SessionStatus(r.status)],
                    badge=lambda r: STATUS_TONES[SessionStatus(r.status)],
                    width=110,
                ),
                Column("Started", lambda r: r.started, width=140),
                Column("Duration", lambda r: r.duration, width=90),
                Column("Position", lambda r: r.position, width=80),
                Column("Dwell", lambda r: r.dwell, width=80),
            ],
        )
        self.export_buttons: dict[str, QPushButton] = {}
        export_row = QHBoxLayout()
        export_row.setSpacing(8)
        self.status_label = QLabel("Choose filters and press Generate report.")
        self.status_label.setObjectName("Muted")
        export_row.addWidget(self.status_label, 1)
        for kind, label in (
            ("csv", "Export CSV"),
            ("excel", "Export Excel"),
            ("json", "Export JSON"),
        ):
            button = QPushButton(label)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, k=kind: self.export_dialog(k))
            self.export_buttons[kind] = button
            export_row.addWidget(button)

        preview_card = Card("Sessions in this report")
        preview_card.body.addWidget(self.table, 1)
        preview_card.body.addLayout(export_row)

        column = QVBoxLayout()
        column.setSpacing(16)
        column.addWidget(filter_card)
        column.addLayout(grid)
        column.addWidget(preview_card, 1)
        self.body.addLayout(column, 1)

    # -- filters ------------------------------------------------------------------- #
    def on_activated(self) -> None:
        self._reload_choices()

    def _reload_choices(self) -> None:
        choices = self._service.filter_choices()

        def fill(combo: Any, first: tuple[str, Any], values: list[tuple[str, Any]]) -> None:
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(*first)
            for label, data in values:
                combo.addItem(label, data)
            combo.setCurrentIndex(max(combo.findData(current), 0))
            combo.blockSignals(False)

        fill(
            self.scenario_filter,
            ("Any scenario", None),
            [(name, sid) for sid, name in choices["scenarios"]],
        )
        fill(self.keyword_filter, ("Any keyword", ""), [(k, k) for k in choices["keywords"]])
        fill(self.target_filter, ("Any target", ""), [(d, d) for d in choices["domains"]])

    def current_filter(self) -> ReportFilter:
        return ReportFilter(
            date_from=self.date_from.value(),
            date_to=self.date_to.value(),
            scenario_id=self.scenario_filter.currentData(),
            keyword=self.keyword_filter.currentData() or "",
            target_domain=self.target_filter.currentData() or "",
            status=_status(self.status_filter.currentData()),
        )

    # -- generating --------------------------------------------------------------------- #
    def generate(self) -> None:
        """Build the report on a worker thread so the window stays responsive."""
        report_filter = self.current_filter()
        if (
            report_filter.date_from
            and report_filter.date_to
            and report_filter.date_from > report_filter.date_to
        ):
            self.context.notifier.error("The start date is after the end date.")
            return
        self._set_busy(True, "Generating report…")
        self._runner.submit(
            lambda: self._service.build(report_filter), self._on_ready, self._on_failed
        )

    def _on_ready(self, result: object) -> None:
        assert isinstance(result, ReportData)
        self.report = result
        values = dict(result.metrics.as_rows())
        for label, card in self.cards.items():
            card.set_value(values[label])
        rows = preview_rows(result)
        self.table.set_rows(rows)
        self._set_busy(False, f"{len(rows)} sessions · generated {result.generated_at:%H:%M:%S}")
        for button in self.export_buttons.values():
            button.setEnabled(True)

    def _on_failed(self, message: str) -> None:
        self._set_busy(False, "The report could not be generated.")
        self.context.notifier.error(message)

    def _set_busy(self, busy: bool, text: str) -> None:
        self.generate_button.setEnabled(not busy)
        self.status_label.setText(text)
        if busy:
            for button in self.export_buttons.values():
                button.setEnabled(False)

    # -- exporting ----------------------------------------------------------------------- #
    def export_dialog(self, kind: str) -> None:
        _, suffix, name_filter = EXPORTERS[kind]
        default = (
            self.context.container.paths.exports_dir
            / f"report_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
        )
        filename, _ = QFileDialog.getSaveFileName(self, "Export report", str(default), name_filter)
        if filename:
            self.export_to(kind, Path(filename))

    def export_to(self, kind: str, path: Path) -> None:
        """Write the current report to ``path`` on a worker thread."""
        if self.report is None:
            return
        exporter = EXPORTERS[kind][0]
        report = self.report
        self._set_busy(True, "Exporting…")

        def done(result: object) -> None:
            self._set_busy(False, f"Exported to {result}")
            for button in self.export_buttons.values():
                button.setEnabled(True)
            self.context.notifier.success(f"Report exported to {Path(str(result)).name}.")

        def failed(message: str) -> None:
            self._on_failed(message)
            for button in self.export_buttons.values():
                button.setEnabled(True)

        self._runner.submit(lambda: exporter(report, path), done, failed)
