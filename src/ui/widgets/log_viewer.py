"""Scrolling, colour-coded event list (used by the live view and the Logs page)."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from src.ui.theme import Palette, ThemeManager

MAX_LINES = 2000
_MONOSPACE = ("Cascadia Mono", "Consolas", "Courier New")


@dataclass(frozen=True, slots=True)
class LogLine:
    time: str
    label: str
    message: str
    tone: str = "neutral"


class LogViewer(QPlainTextEdit):
    """Read-only monospace viewer. Lines keep their tone colour across theme changes."""

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._lines: list[LogLine] = []
        self.setReadOnly(True)
        self.setMaximumBlockCount(MAX_LINES)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        font = QFont()
        font.setFamilies(list(_MONOSPACE))
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSizeF(9.0)
        self.setFont(font)
        theme.theme_changed.connect(self._rerender)

    def add_line(self, line: LogLine) -> None:
        self._lines.append(line)
        del self._lines[:-MAX_LINES]
        self.appendHtml(self._html(line, self._theme.palette))

    def set_lines(self, lines: list[LogLine]) -> None:
        self._lines = list(lines)[-MAX_LINES:]
        self._rerender(self._theme.palette)

    def clear_lines(self) -> None:
        self._lines.clear()
        self.clear()

    def line_count(self) -> int:
        return len(self._lines)

    def plain_lines(self) -> list[str]:
        return [f"{line.time}  {line.label}  {line.message}" for line in self._lines]

    def _rerender(self, palette: Palette) -> None:
        self.setUpdatesEnabled(False)
        self.clear()
        for line in self._lines:
            self.appendHtml(self._html(line, palette))
        self.setUpdatesEnabled(True)

    @staticmethod
    def _html(line: LogLine, palette: Palette) -> str:
        colour, _ = palette.tone(line.tone)
        return (
            f'<span style="color:{palette.text_faint}">{escape(line.time)}</span>&nbsp;&nbsp;'
            f'<span style="color:{colour};font-weight:600">{escape(line.label)}</span>&nbsp;&nbsp;'
            f'<span style="color:{palette.text}">{escape(line.message)}</span>'
        )
