"""Transient, non-blocking notifications shown in a corner of the main window."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QWidget

from src.ui.widgets.status_badge import BadgeTone, StatusBadge

_MARGIN = 20
_DEFAULT_TIMEOUT_MS = 3500
_ERROR_TIMEOUT_MS = 7000
_MAX_WIDTH = 460
_TONE_LABELS: dict[BadgeTone, str] = {
    BadgeTone.SUCCESS: "Done",
    BadgeTone.WARNING: "Note",
    BadgeTone.DANGER: "Error",
    BadgeTone.INFO: "Info",
    BadgeTone.NEUTRAL: "Info",
}


class ToastNotification(QFrame):
    """The visual toast: a tone badge followed by a short message."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("Toast")
        self.setMaximumWidth(_MAX_WIDTH)
        self._badge = StatusBadge()
        self._text = QLabel()
        self._text.setObjectName("ToastText")
        self._text.setWordWrap(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 14, 10)
        layout.setSpacing(10)
        layout.addWidget(self._badge)
        layout.addWidget(self._text, 1)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)
        self.hide()

    def set_content(self, message: str, tone: BadgeTone) -> None:
        self._badge.set_status(_TONE_LABELS[tone], tone)
        self._text.setText(message)
        self.adjustSize()

    def message(self) -> str:
        return self._text.text()

    def tone(self) -> str:
        return str(self._badge.property("tone"))


class Notifier(QObject):
    """Shows one toast at a time, anchored to the bottom-right of ``host``."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self.toast = ToastNotification(host)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.toast.hide)
        host.installEventFilter(self)

    def show_message(self, message: str, tone: BadgeTone = BadgeTone.SUCCESS) -> None:
        self.toast.set_content(message, tone)
        self._reposition()
        self.toast.show()
        self.toast.raise_()
        self._timer.start(_ERROR_TIMEOUT_MS if tone is BadgeTone.DANGER else _DEFAULT_TIMEOUT_MS)

    def success(self, message: str) -> None:
        self.show_message(message, BadgeTone.SUCCESS)

    def error(self, message: str) -> None:
        self.show_message(message, BadgeTone.DANGER)

    def info(self, message: str) -> None:
        self.show_message(message, BadgeTone.INFO)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self) -> None:
        size = self.toast.sizeHint()
        self.toast.move(
            self._host.width() - size.width() - _MARGIN,
            self._host.height() - size.height() - _MARGIN - 24,
        )
