"""Friendly placeholder for lists and pages that have nothing to show yet."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from src.ui.icons import make_pixmap
from src.ui.theme import Palette, ThemeManager

_ICON_SIZE = 40
_DESCRIPTION_WIDTH = 440


class EmptyState(QWidget):
    """Centered icon, title, explanation and an optional call-to-action button."""

    def __init__(
        self,
        theme: ThemeManager,
        icon: str,
        title: str,
        description: str = "",
        action_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel(title)
        self._title.setObjectName("EmptyTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._description = QLabel(description)
        self._description.setObjectName("EmptyDescription")
        self._description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._description.setWordWrap(True)
        self._description.setFixedWidth(_DESCRIPTION_WIDTH)
        self._description.setVisible(bool(description))

        self.action_button: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addStretch(1)
        layout.addWidget(self._icon)
        layout.addWidget(self._title)
        layout.addWidget(self._description, 0, Qt.AlignmentFlag.AlignHCenter)
        if action_text:
            self.action_button = QPushButton(action_text)
            self.action_button.setProperty("variant", "primary")
            layout.addSpacing(6)
            layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        self._apply_palette(theme.palette)
        theme.theme_changed.connect(self._apply_palette)

    def set_text(self, title: str, description: str = "") -> None:
        self._title.setText(title)
        self._description.setText(description)
        self._description.setVisible(bool(description))

    def _apply_palette(self, palette: Palette) -> None:
        self._icon.setPixmap(make_pixmap(self._icon_name, palette.text_faint, _ICON_SIZE))
