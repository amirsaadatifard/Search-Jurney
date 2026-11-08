"""Start / Pause / Resume / Stop buttons plus a status badge, bound to a run controller."""

from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from src.ui.icons import make_icon
from src.ui.run_controller import RunController, RunPhase
from src.ui.theme import Palette, ThemeManager
from src.ui.widgets.status_badge import BadgeTone, StatusBadge

_ICON_SIZE = 16
_PHASE_BADGES: dict[RunPhase, tuple[str, BadgeTone]] = {
    RunPhase.IDLE: ("Idle", BadgeTone.NEUTRAL),
    RunPhase.RUNNING: ("Running", BadgeTone.SUCCESS),
    RunPhase.PAUSED: ("Paused", BadgeTone.WARNING),
    RunPhase.STOPPING: ("Stopping…", BadgeTone.WARNING),
}


class RunControls(QWidget):
    """The four run buttons. ``start_requested`` is emitted when Start is clicked."""

    start_requested = Signal()

    def __init__(
        self,
        controller: RunController,
        theme: ThemeManager,
        show_start: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._can_start = True

        self.start_button = self._button("Start", "play", "primary")
        self.pause_button = self._button("Pause", "pause")
        self.resume_button = self._button("Resume", "play")
        self.stop_button = self._button("Stop", "stop", "danger")
        self.badge = StatusBadge()
        self.start_button.setVisible(show_start)

        self.start_button.clicked.connect(self.start_requested)
        self.pause_button.clicked.connect(controller.pause)
        self.resume_button.clicked.connect(controller.resume)
        self.stop_button.clicked.connect(controller.stop)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            layout.addWidget(button)
        layout.addSpacing(6)
        layout.addWidget(self.badge)
        layout.addStretch(1)

        controller.changed.connect(self.refresh)
        theme.theme_changed.connect(self._apply_icons)
        self._apply_icons(theme.palette)
        self.refresh()

    def _button(self, text: str, icon: str, variant: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setProperty("variant", variant or None)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setProperty("icon_name", icon)
        return button

    def set_can_start(self, allowed: bool) -> None:
        """Whether a run can be started right now (e.g. at least one scenario exists)."""
        self._can_start = allowed
        self.refresh()

    def set_start_text(self, text: str) -> None:
        self.start_button.setText(text)

    def refresh(self) -> None:
        controller = self._controller
        self.start_button.setEnabled(self._can_start and not controller.is_active)
        self.pause_button.setEnabled(controller.can_pause)
        self.resume_button.setEnabled(controller.can_resume)
        self.stop_button.setEnabled(controller.can_stop)
        text, tone = _PHASE_BADGES[controller.phase]
        self.badge.set_status(text, tone)

    def _apply_icons(self, palette: Palette) -> None:
        colours = {"primary": palette.accent_text, "danger": palette.danger}
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            colour = colours.get(str(button.property("variant")), palette.text_muted)
            button.setIcon(make_icon(str(button.property("icon_name")), colour, size=_ICON_SIZE))
