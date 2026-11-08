"""Application log viewer (live, filterable)."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.app.constants import UI_LOG_BUFFER_SIZE
from src.logging_config import LogEntry
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.widgets.card import Card
from src.ui.widgets.form_dialog import make_combo
from src.ui.widgets.log_viewer import LogLine, LogViewer
from src.ui.widgets.search_box import SearchBox

LEVEL_ORDER: Final[dict[str, int]] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
LEVEL_TONES: Final[dict[str, str]] = {
    "ERROR": "danger",
    "CRITICAL": "danger",
    "WARNING": "warning",
    "INFO": "info",
    "DEBUG": "neutral",
}
LEVEL_CHOICES: Final = (
    ("All levels", 0),
    ("Info and above", LEVEL_ORDER["INFO"]),
    ("Warnings and errors", LEVEL_ORDER["WARNING"]),
    ("Errors only", LEVEL_ORDER["ERROR"]),
)


class _LogBridge(QObject):
    """Moves log entries from whatever thread logged them to the GUI thread."""

    entry = Signal(object)


class LogsPage(BasePage):
    """Shows the in-memory log buffer live; the same records go to the rotating file."""

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context, "Logs", "Structured application and automation logs, newest at the bottom."
        )
        self._buffer = context.container.log_buffer
        self._entries: list[LogEntry] = list(self._buffer.entries())

        self.search_box = SearchBox(context.theme, "Filter log text…")
        self.search_box.search_changed.connect(lambda _text: self._render())
        self.level_filter = make_combo(list(LEVEL_CHOICES))
        self.level_filter.setCurrentIndex(1)
        self.level_filter.currentIndexChanged.connect(lambda _index: self._render())
        clear = QPushButton("Clear view")
        clear.clicked.connect(self.clear_view)
        folder = QPushButton("Open log folder")
        folder.clicked.connect(self._open_folder)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self.search_box)
        toolbar.addWidget(self.level_filter)
        toolbar.addStretch(1)
        toolbar.addWidget(clear)
        toolbar.addWidget(folder)

        self.viewer = LogViewer(context.theme)
        self.footer = QLabel()
        self.footer.setObjectName("TableFooter")
        card = Card()
        card.body.addWidget(self.viewer, 1)
        column = QVBoxLayout()
        column.setSpacing(10)
        column.addLayout(toolbar)
        column.addWidget(card, 1)
        column.addWidget(self.footer)
        self.body.addLayout(column, 1)

        self._bridge = _LogBridge(self)
        self._bridge.entry.connect(self._on_entry)
        self._listener = self._bridge.entry.emit
        self._buffer.subscribe(self._listener)
        self.destroyed.connect(lambda: self._buffer.unsubscribe(self._listener))
        self._render()

    # -- data --------------------------------------------------------------------- #
    def _matches(self, entry: LogEntry) -> bool:
        minimum = int(self.level_filter.currentData() or 0)
        if LEVEL_ORDER.get(entry.level, 0) < minimum:
            return False
        needle = self.search_box.text().strip().lower()
        return not needle or needle in f"{entry.logger} {entry.message}".lower()

    @staticmethod
    def _line(entry: LogEntry) -> LogLine:
        return LogLine(
            entry.timestamp.strftime("%H:%M:%S"),
            entry.level.ljust(7),
            f"{entry.logger.removeprefix('src.')} · {entry.message}",
            LEVEL_TONES.get(entry.level, "neutral"),
        )

    def _on_entry(self, entry: LogEntry) -> None:
        self._entries.append(entry)
        del self._entries[:-UI_LOG_BUFFER_SIZE]
        if self._matches(entry):
            self.viewer.add_line(self._line(entry))
            self._update_footer()

    def _render(self) -> None:
        lines = [self._line(entry) for entry in self._entries if self._matches(entry)]
        self.viewer.set_lines(lines)
        self._update_footer()

    def _update_footer(self) -> None:
        self.footer.setText(
            f"{self.viewer.line_count()} of {len(self._entries)} entries shown · "
            f"log file: {self.context.container.paths.log_file}"
        )

    def clear_view(self) -> None:
        """Empty the view and the in-memory buffer (the log file is not touched)."""
        self._entries.clear()
        self._buffer.clear()
        self._render()

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.container.paths.logs_dir)))
