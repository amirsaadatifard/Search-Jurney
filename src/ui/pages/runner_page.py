"""Automation runner: choose scenarios, then start / pause / resume / stop the run."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from src.database.models import Scenario
from src.domain.exceptions import AppError
from src.ui.navigation import PageKey
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.run_controller import STATE_LABELS, RunController, TimelineEntry
from src.ui.widgets.card import Card
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.log_viewer import LogLine, LogViewer
from src.ui.widgets.run_controls import RunControls
from src.ui.widgets.status_badge import BadgeTone, StatusBadge

PROGRESS_STEPS = 1000


def _dwell_text(scenario: Scenario) -> str:
    return f"{scenario.min_dwell_seconds}–{scenario.max_dwell_seconds} s"


class RunnerPage(BasePage):
    """Scenario selection, run controls, overall progress and the event timeline."""

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Automation Runner",
            "Run scenarios against the controlled test environment. Nothing leaves this computer.",
        )
        self._runs: RunController = context.runs
        theme = context.theme

        self.controls = RunControls(self._runs, theme)
        self.controls.start_requested.connect(self.start_run)
        control_card = Card("Run control")
        control_card.body.addWidget(self.controls)
        self.status_line = QLabel()
        self.status_line.setObjectName("Muted")
        self.status_line.setWordWrap(True)
        control_card.body.addWidget(self.status_line)

        self.table: DataTable[Scenario] = DataTable(
            theme,
            [
                Column("Scenario", lambda s: s.name, stretch=True),
                Column("Keyword", lambda s: s.keyword, stretch=True),
                Column("Dwell", _dwell_text, lambda s: s.min_dwell_seconds, width=90),
                Column("Runs", lambda s: str(s.iterations), lambda s: s.iterations, width=60),
            ],
        )
        self.table.selection_changed.connect(self._update_start_button)
        self.empty_state = EmptyState(
            theme,
            "scenarios",
            "No enabled scenarios",
            "Create and enable a scenario first, then come back to run it.",
            "Go to Scenarios",
        )
        if self.empty_state.action_button is not None:
            self.empty_state.action_button.clicked.connect(
                lambda: context.navigate(PageKey.SCENARIOS)
            )
        self.hint = QLabel("Select scenarios to run, or start without a selection to run all.")
        self.hint.setObjectName("Faint")

        scenarios_card = Card("Scenarios")
        scenarios_card.body.addWidget(self.table, 1)
        scenarios_card.body.addWidget(self.empty_state, 1)
        scenarios_card.body.addWidget(self.hint)

        self.progress = QProgressBar()
        self.progress.setRange(0, PROGRESS_STEPS)
        self.progress.setTextVisible(False)
        self.progress_text = QLabel("No run yet")
        self.progress_text.setObjectName("Muted")
        self.counters = {
            "completed": StatusBadge("0 completed", BadgeTone.SUCCESS),
            "target_not_found": StatusBadge("0 not found", BadgeTone.WARNING),
            "failed": StatusBadge("0 failed", BadgeTone.DANGER),
            "stopped": StatusBadge("0 stopped", BadgeTone.NEUTRAL),
        }
        badges = QHBoxLayout()
        badges.setSpacing(8)
        for badge in self.counters.values():
            badges.addWidget(badge)
        badges.addStretch(1)
        progress_card = Card("Progress")
        progress_card.body.addWidget(self.progress)
        progress_card.body.addWidget(self.progress_text)
        progress_card.body.addLayout(badges)

        self.timeline = LogViewer(theme)
        timeline_card = Card("Event timeline")
        timeline_card.body.addWidget(self.timeline, 1)
        watch = QLabel("Open Live Session View to watch the browser while it runs.")
        watch.setObjectName("Faint")
        timeline_card.body.addWidget(watch)

        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(control_card)
        left.addWidget(scenarios_card, 1)
        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(progress_card)
        right.addWidget(timeline_card, 1)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(left, 5)
        columns.addLayout(right, 4)
        self.body.addLayout(columns, 1)

        self._runs.changed.connect(self._refresh_status)
        self._runs.entry_added.connect(self._add_entry)
        self._runs.started.connect(self.timeline.clear_lines)
        self._refresh_status()

    # -- data -------------------------------------------------------------------- #
    def on_activated(self) -> None:
        self.reload_scenarios()
        self._refresh_status()

    def reload_scenarios(self) -> None:
        scenarios = self.context.container.scenarios.search(enabled=True)
        self.table.set_rows(scenarios)
        has_rows = bool(scenarios)
        self.table.setVisible(has_rows)
        self.hint.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        self._update_start_button()

    def selected_scenario_ids(self) -> list[int]:
        """Selected scenarios, or every enabled scenario when nothing is selected."""
        chosen = self.table.selected_objects()
        if not chosen:
            chosen = [self.table.object_at_position(i) for i in range(self.table.row_count())]
        return [scenario.id for scenario in chosen]

    def _update_start_button(self) -> None:
        count = len(self.table.selected_objects())
        total = self.table.row_count()
        self.controls.set_can_start(total > 0)
        if count:
            self.controls.set_start_text(f"Start {count} selected")
        else:
            self.controls.set_start_text("Start all enabled" if total else "Start")

    # -- actions ----------------------------------------------------------------- #
    def start_run(self) -> None:
        """Start the run, then show the live view so the user can watch it."""
        ids = self.selected_scenario_ids()
        if not self.run_guarded(lambda: self._runs.start(ids)):
            return
        self.context.navigate(PageKey.LIVE_VIEW)

    def run_guarded(self, action: Callable[[], None]) -> bool:
        """Run ``action``; report expected errors as a toast instead of raising."""
        try:
            action()
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return False
        return True

    # -- rendering ---------------------------------------------------------------- #
    def _add_entry(self, entry: TimelineEntry) -> None:
        self.timeline.add_line(LogLine(entry.time, entry.event_type, entry.message, entry.tone))

    def _refresh_status(self) -> None:
        runs = self._runs
        self.progress.setValue(round(runs.progress() * PROGRESS_STEPS))
        summary = runs.summary
        self.counters["completed"].setText(f"{summary.completed} completed")
        self.counters["target_not_found"].setText(f"{summary.target_not_found} not found")
        self.counters["failed"].setText(f"{summary.failed} failed")
        self.counters["stopped"].setText(f"{summary.stopped} stopped")

        if not runs.has_run:
            self.progress_text.setText("No run yet")
            self.status_line.setText("Ready. Choose scenarios and press Start.")
            return
        position = min(runs.done_sessions + (1 if runs.is_active else 0), runs.total_sessions)
        self.progress_text.setText(f"Session {position} of {runs.total_sessions}")
        if runs.is_active:
            step = STATE_LABELS.get(runs.state, "") if runs.state else ""
            self.status_line.setText(
                f"{runs.scenario_name} · iteration {runs.iteration}/{runs.total_iterations} · "
                f"{runs.step or step}"
            )
        else:
            self.status_line.setText(
                f"Finished: {summary.completed} completed, {summary.target_not_found} not found, "
                f"{summary.failed} failed, {summary.stopped} stopped."
            )
