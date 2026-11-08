"""A card that holds one chart, with a switch to the same data as a table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedWidget, QWidget

from src.ui.theme import ThemeManager
from src.ui.widgets.card import Card
from src.ui.widgets.data_table import Column, DataTable


class TabularChart(Protocol):
    """What a chart must offer so it can be shown as a table."""

    def rows(self) -> list[tuple[str, str]]: ...


@dataclass(frozen=True, slots=True)
class _Row:
    label: str
    value: str


class ChartCard(Card):
    """Title, one-line explanation, the chart, and a *Table view* toggle."""

    def __init__(
        self,
        theme: ThemeManager,
        title: str,
        subtitle: str,
        chart: QWidget,
        value_header: str = "Value",
    ) -> None:
        super().__init__()
        self.chart = chart
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        self.toggle = QPushButton("Table view")
        self.toggle.setProperty("variant", "ghost")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._show_table)
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(self.toggle)

        note = QLabel(subtitle)
        note.setObjectName("Faint")
        note.setWordWrap(True)

        self.table: DataTable[_Row] = DataTable(
            theme,
            [
                Column("Item", lambda r: r.label, stretch=True),
                Column(value_header, lambda r: r.value, width=140),
            ],
        )
        self.table.keep_source_order()
        self.stack = QStackedWidget()
        self.stack.addWidget(chart)
        self.stack.addWidget(self.table)

        self.body.addLayout(header)
        self.body.addWidget(note)
        self.body.addWidget(self.stack, 1)

    def refresh_table(self) -> None:
        rows = self.chart.rows() if hasattr(self.chart, "rows") else []
        self.table.set_rows([_Row(label, value) for label, value in rows])

    def _show_table(self, checked: bool) -> None:
        if checked:
            self.refresh_table()
        self.stack.setCurrentIndex(1 if checked else 0)
        self.toggle.setText("Chart view" if checked else "Table view")
