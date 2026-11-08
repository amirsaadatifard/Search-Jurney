"""Shows the latest live-preview frame, scaled to fit."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

MIN_PREVIEW_SIZE = QSize(360, 225)


class PreviewPane(QLabel):
    """A picture of the browser page; shows a hint until the first frame arrives."""

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(placeholder, parent)
        self._placeholder = placeholder
        self._pixmap: QPixmap | None = None
        self.setObjectName("PreviewPane")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(MIN_PREVIEW_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setWordWrap(True)

    def show_frame(self, data: bytes) -> bool:
        """Display a JPEG/PNG frame. Returns ``False`` if the data was not an image."""
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return False
        self._pixmap = pixmap
        self._fit()
        return True

    def clear_frame(self) -> None:
        self._pixmap = None
        self.setPixmap(QPixmap())
        self.setText(self._placeholder)

    @property
    def has_frame(self) -> bool:
        return self._pixmap is not None

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self._pixmap is None:
            return
        self.setPixmap(
            self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
