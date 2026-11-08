"""Search input with an icon, a clear button and debounced change notification."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QLineEdit, QWidget

from src.ui.icons import make_icon
from src.ui.theme import Palette, ThemeManager

_DEBOUNCE_MS = 250


class SearchBox(QLineEdit):
    """Emits :attr:`search_changed` shortly after the user stops typing."""

    search_changed = Signal(str)

    def __init__(
        self, theme: ThemeManager, placeholder: str = "Search…", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(240)
        self._icon_action = self.addAction(
            make_icon("search", theme.palette.text_faint), QLineEdit.ActionPosition.LeadingPosition
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(lambda: self.search_changed.emit(self.text().strip()))
        self.textChanged.connect(lambda _text: self._timer.start())
        theme.theme_changed.connect(self._apply_palette)

    def _apply_palette(self, palette: Palette) -> None:
        self._icon_action.setIcon(make_icon("search", palette.text_faint))
