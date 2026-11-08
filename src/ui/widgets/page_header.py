"""Title area shown at the top of every page."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


class PageHeader(QWidget):
    """Page title, one-line description and a right-aligned action area."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel(title)
        self._title.setObjectName("PageTitle")
        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("PageSubtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(subtitle))

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        text_column.addWidget(self._title)
        text_column.addWidget(self._subtitle)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(text_column, 1)
        layout.addLayout(self._actions)

    def add_action(self, widget: QWidget) -> None:
        """Append a widget (usually a button) to the action area."""
        self._actions.addWidget(widget)

    def insert_action_before_last(self, widget: QWidget) -> None:
        """Insert a widget left of the right-most (primary) action."""
        self._actions.insertWidget(max(self._actions.count() - 1, 0), widget)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))
