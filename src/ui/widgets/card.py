"""Card container and small layout helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

CARD_PADDING = 18
CARD_SPACING = 12


class Card(QFrame):
    """A bordered surface that groups related content, optionally titled."""

    def __init__(self, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        self._layout.setSpacing(CARD_SPACING)
        if title:
            heading = QLabel(title)
            heading.setObjectName("SectionTitle")
            heading.setWordWrap(True)
            self._layout.addWidget(heading)

    @property
    def body(self) -> QVBoxLayout:
        """Layout to which callers add the card content."""
        return self._layout


def divider(parent: QWidget | None = None) -> QFrame:
    """Return a 1px horizontal separator."""
    line = QFrame(parent)
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    return line
