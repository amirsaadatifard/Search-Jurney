"""Pill-shaped status label."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class BadgeTone(StrEnum):
    """Semantic colour of a badge (mapped to palette tokens in the stylesheet)."""

    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"
    NEUTRAL = "neutral"


class StatusBadge(QLabel):
    """Small coloured label such as *Enabled*, *Failed* or *Running*."""

    def __init__(
        self, text: str = "", tone: BadgeTone = BadgeTone.NEUTRAL, parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def set_tone(self, tone: BadgeTone) -> None:
        """Change the colour; re-polishes so the stylesheet picks up the property."""
        self.setProperty("tone", tone.value)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_status(self, text: str, tone: BadgeTone) -> None:
        self.setText(text)
        self.set_tone(tone)
