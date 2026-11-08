"""Live view of the running session: preview, current step, countdown and timeline."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.formatting import PLACEHOLDER, format_seconds
from src.ui.navigation import PageKey
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.run_controller import STATE_LABELS, RunController, RunPhase, TimelineEntry
from src.ui.widgets.card import Card
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.log_viewer import LogLine, LogViewer
from src.ui.widgets.preview_pane import PreviewPane
from src.ui.widgets.run_controls import RunControls
from src.ui.widgets.status_badge import BadgeTone, StatusBadge

CLOCK_INTERVAL_MS = 250
COUNTDOWN_STEPS = 1000
PREVIEW_PLACEHOLDER = "The browser view appears here as soon as the session reaches a page."
_PHASE_TONES: dict[RunPhase, BadgeTone] = {
    RunPhase.IDLE: BadgeTone.NEUTRAL,
    RunPhase.RUNNING: BadgeTone.SUCCESS,
    RunPhase.PAUSED: BadgeTone.WARNING,
    RunPhase.STOPPING: BadgeTone.WARNING,
}


class LiveViewPage(BasePage):
    """What is happening right now. Everything shown comes from the run controller."""

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Live Session View",
            "Watch the current step, page and dwell countdown while a session runs.",
        )
        self._runs: RunController = context.runs
        theme = context.theme

        self.empty_state = EmptyState(
            theme,
            "live",
            "No run in progress",
            "Start a run from the Automation Runner and it appears here, step by step.",
            "Open Automation Runner",
        )
        if self.empty_state.action_button is not None:
            self.empty_state.action_button.clicked.connect(lambda: context.navigate(PageKey.RUNNER))
        empty_card = Card()
        empty_card.body.addWidget(self.empty_state, 1)

        self.controls = RunControls(self._runs, theme, show_start=False)
        self.preview = PreviewPane(PREVIEW_PLACEHOLDER)
        preview_card = Card("Browser view")
        preview_card.body.addWidget(self.preview, 1)

        self.timeline = LogViewer(theme)
        timeline_card = Card("Event timeline")
        timeline_card.body.addWidget(self.timeline, 1)

        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(self.controls)
        left.addWidget(preview_card, 3)
        left.addWidget(timeline_card, 2)

        live = QWidget()
        columns = QHBoxLayout(live)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(16)
        columns.addLayout(left, 5)
        columns.addWidget(self._build_details(), 3)

        self._stack = QStackedWidget()
        self._stack.addWidget(empty_card)
        self._stack.addWidget(live)
        self.body.addWidget(self._stack, 1)

        self._clock = QTimer(self)
        self._clock.setInterval(CLOCK_INTERVAL_MS)
        self._clock.timeout.connect(self._tick)
        self._runs.changed.connect(self.refresh)
        self._runs.entry_added.connect(self._add_entry)
        self._runs.preview.connect(self._show_preview)
        self._runs.started.connect(self._on_started)
        self._clock.start()
        self.refresh()

    # -- construction ------------------------------------------------------------- #
    def _build_details(self) -> Card:
        card = Card("Session")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(1, 1)
        self.fields: dict[str, QLabel] = {}
        rows = (
            ("scenario", "Scenario"),
            ("keyword", "Keyword"),
            ("iteration", "Iteration"),
            ("step", "Current step"),
            ("url", "Current URL"),
            ("position", "Target position"),
            ("duration", "Session duration"),
            ("browser", "Browser profile"),
            ("proxy", "Proxy profile"),
        )
        for row, (key, caption) in enumerate(rows):
            label = QLabel(caption)
            label.setObjectName("Muted")
            value = QLabel(PLACEHOLDER)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self.fields[key] = value
        card.body.addLayout(grid)

        self.status_badge = StatusBadge("Idle", BadgeTone.NEUTRAL)
        status_row = QHBoxLayout()
        status_caption = QLabel("Status")
        status_caption.setObjectName("Muted")
        status_row.addWidget(status_caption)
        status_row.addWidget(self.status_badge)
        status_row.addStretch(1)
        card.body.addLayout(status_row)

        self.countdown_label = QLabel("Dwell countdown")
        self.countdown_label.setObjectName("Muted")
        self.countdown_value = QLabel(PLACEHOLDER)
        self.countdown_value.setObjectName("MetricValue")
        self.countdown = QProgressBar()
        self.countdown.setRange(0, COUNTDOWN_STEPS)
        self.countdown.setTextVisible(False)
        card.body.addSpacing(6)
        card.body.addWidget(self.countdown_label)
        card.body.addWidget(self.countdown_value)
        card.body.addWidget(self.countdown)

        self.overall = QProgressBar()
        self.overall.setRange(0, COUNTDOWN_STEPS)
        self.overall.setTextVisible(False)
        self.overall_text = QLabel()
        self.overall_text.setObjectName("Faint")
        card.body.addSpacing(6)
        card.body.addWidget(QLabel("Overall progress"))
        card.body.addWidget(self.overall)
        card.body.addWidget(self.overall_text)
        card.body.addStretch(1)
        return card

    # -- updates -------------------------------------------------------------------- #
    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Render the controller state into the widgets."""
        runs = self._runs
        self._stack.setCurrentIndex(1 if (runs.is_active or runs.has_run) else 0)
        if not (runs.is_active or runs.has_run):
            return

        iteration = (
            f"{runs.iteration} of {runs.total_iterations}" if runs.total_iterations else PLACEHOLDER
        )
        if runs.scenario_total > 1:
            iteration += f"  (scenario {runs.scenario_index}/{runs.scenario_total})"
        position = PLACEHOLDER
        if runs.target_resolved:
            position = "Not listed" if runs.target_position is None else f"#{runs.target_position}"

        self._set("scenario", runs.scenario_name)
        self._set("keyword", runs.keyword)
        self._set("iteration", iteration)
        self._set("step", runs.step or (STATE_LABELS.get(runs.state, "") if runs.state else ""))
        self._set("url", runs.url)
        self._set("position", position)
        self._set("browser", runs.browser_name)
        self._set("proxy", runs.proxy_name)
        self._tick()

        phase = runs.phase
        label = {
            RunPhase.IDLE: "Finished",
            RunPhase.RUNNING: "Running",
            RunPhase.PAUSED: "Paused",
            RunPhase.STOPPING: "Stopping…",
        }[phase]
        self.status_badge.set_status(label, _PHASE_TONES[phase])
        self.overall.setValue(round(runs.progress() * COUNTDOWN_STEPS))
        if runs.is_active:
            current = min(runs.done_sessions + 1, runs.total_sessions)
            self.overall_text.setText(f"Session {current} of {runs.total_sessions}")
        else:
            self.overall_text.setText(
                f"{runs.done_sessions} of {runs.total_sessions} sessions finished"
            )

    def _set(self, key: str, text: str) -> None:
        self.fields[key].setText(text or PLACEHOLDER)

    def _tick(self) -> None:
        """Update the values that change continuously (clock and countdown)."""
        runs = self._runs
        self.fields["duration"].setText(
            format_seconds(runs.session_elapsed())
            if runs.has_run and runs.iteration
            else PLACEHOLDER
        )
        if runs.dwell_remaining is None or not runs.dwell_total:
            self.countdown_value.setText(PLACEHOLDER)
            self.countdown.setValue(0)
            return
        self.countdown_value.setText(f"{runs.dwell_remaining:.1f} s")
        self.countdown.setValue(round(runs.dwell_fraction() * COUNTDOWN_STEPS))

    def _on_started(self) -> None:
        self.timeline.clear_lines()
        self.preview.clear_frame()

    def _add_entry(self, entry: TimelineEntry) -> None:
        self.timeline.add_line(LogLine(entry.time, entry.event_type, entry.message, entry.tone))

    def _show_preview(self, frame: object) -> None:
        if isinstance(frame, bytes):
            self.preview.show_frame(frame)
