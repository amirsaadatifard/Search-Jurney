"""Bridge between the automation thread and the GUI.

``QtRunObserver`` is called on the automation thread and re-emits everything as
Qt signals; because its receivers live in the GUI thread, Qt delivers them
there through the event queue. ``RunController`` collects those signals into
one observable model that the Runner and Live View pages render, so both pages
always agree and neither touches the automation layer directly.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from PySide6.QtCore import QObject, Signal

from src.automation.models import RunSummary, SessionPlan, SessionResult
from src.automation.observer import RunObserver
from src.automation.run_service import AutomationRunService
from src.domain.enums import EventType, SessionState, SessionStatus
from src.domain.exceptions import AutomationError

MAX_EVENTS: Final = 2000
NO_PROXY_LABEL: Final = "Direct connection"

STATE_LABELS: Final[dict[SessionState, str]] = {
    SessionState.IDLE: "Idle",
    SessionState.INITIALIZING: "Checking safety rules",
    SessionState.STARTING_BROWSER: "Starting the browser",
    # OPENING_SEARCH and ENTERING_KEYWORD belong to the browser-search flow of
    # earlier versions; they only appear when an old session is opened.
    SessionState.OPENING_SEARCH: "Opening the search page",
    SessionState.ENTERING_KEYWORD: "Typing the keyword",
    SessionState.SEARCHING: "Reading the Google ranking",
    SessionState.ANALYZING_RESULTS: "Looking for the target site",
    SessionState.TARGET_FOUND: "Target found",
    SessionState.TARGET_NOT_FOUND: "Target not found",
    SessionState.OPENING_TARGET: "Opening the target",
    SessionState.DWELLING: "Dwelling on the target page",
    SessionState.INTERACTING: "Interacting with the page",
    SessionState.CAPTURING_RESULT: "Saving the result",
    SessionState.COMPLETED: "Completed",
    SessionState.FAILED: "Failed",
    SessionState.PAUSED: "Paused",
    SessionState.STOPPED: "Stopped",
}

# Fraction of one session that is done when it enters a state (dwelling is dynamic).
_STATE_PROGRESS: Final[dict[SessionState, float]] = {
    SessionState.INITIALIZING: 0.02,
    SessionState.SEARCHING: 0.08,
    SessionState.ANALYZING_RESULTS: 0.18,
    SessionState.STARTING_BROWSER: 0.34,
    SessionState.OPENING_SEARCH: 0.15,
    SessionState.ENTERING_KEYWORD: 0.22,
    SessionState.TARGET_FOUND: 0.26,
    SessionState.OPENING_TARGET: 0.40,
    SessionState.TARGET_NOT_FOUND: 0.90,
    SessionState.CAPTURING_RESULT: 0.93,
}
_DWELL_START: Final = 0.50
_DWELL_SPAN: Final = 0.40
_FINAL_STATES: Final = frozenset(
    {SessionState.COMPLETED, SessionState.FAILED, SessionState.STOPPED}
)

_EVENT_TONES: Final[dict[str, str]] = {
    EventType.ERROR.value: "danger",
    EventType.SESSION_FAILED.value: "danger",
    EventType.SAFETY_BLOCKED.value: "danger",
    EventType.TARGET_FOUND.value: "success",
    EventType.SESSION_COMPLETED.value: "success",
    EventType.TARGET_NOT_FOUND.value: "warning",
    EventType.PAUSED.value: "warning",
    EventType.RESUMED.value: "info",
    EventType.SESSION_STOPPED.value: "warning",
    EventType.SCREENSHOT_CAPTURED.value: "info",
}


def session_progress(state: SessionState | None, dwell_fraction: float = 0.0) -> float:
    """How far through one session we are (0..1) for a given state."""
    if state is None or state is SessionState.IDLE:
        return 0.0
    if state in _FINAL_STATES:
        return 1.0
    if state in {SessionState.DWELLING, SessionState.INTERACTING}:
        return _DWELL_START + _DWELL_SPAN * min(max(dwell_fraction, 0.0), 1.0)
    return _STATE_PROGRESS.get(state, 0.0)


def event_tone(event_type: str) -> str:
    return _EVENT_TONES.get(event_type, "neutral")


class RunPhase(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    time: str
    event_type: str
    message: str
    tone: str


class QtRunObserver(QObject, RunObserver):
    """Turns observer callbacks (automation thread) into Qt signals (GUI thread)."""

    run_started = Signal(object)
    scenario_started = Signal(object, int, int)
    session_started = Signal(int, int, int)
    state_changed = Signal(object, str)
    event_logged = Signal(str, str, object)
    page_changed = Signal(str)
    target_position = Signal(object)
    dwell_tick = Signal(float, float)
    preview = Signal(object)
    waiting = Signal(float)
    session_finished = Signal(object)
    run_finished = Signal(object)

    def on_run_started(self, plans: list[SessionPlan]) -> None:
        self.run_started.emit(plans)

    def on_scenario_started(self, plan: SessionPlan, index: int, total: int) -> None:
        self.scenario_started.emit(plan, index, total)

    def on_session_started(self, session_id: int, iteration: int, total: int) -> None:
        self.session_started.emit(session_id, iteration, total)

    def on_state_changed(self, state: SessionState, message: str) -> None:
        self.state_changed.emit(state, message)

    def on_event(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        self.event_logged.emit(event_type, message, data)

    def on_page_changed(self, url: str) -> None:
        self.page_changed.emit(url)

    def on_target_position(self, position: int | None) -> None:
        self.target_position.emit(position)

    def on_dwell_tick(self, remaining_seconds: float, planned_seconds: float) -> None:
        self.dwell_tick.emit(remaining_seconds, planned_seconds)

    def on_preview(self, jpeg: bytes) -> None:
        self.preview.emit(jpeg)

    def on_waiting(self, seconds: float) -> None:
        self.waiting.emit(seconds)

    def on_session_finished(self, result: SessionResult) -> None:
        self.session_finished.emit(result)

    def on_run_finished(self, summary: RunSummary) -> None:
        self.run_finished.emit(summary)


class RunController(QObject):
    """Observable state of the current (or most recent) run."""

    changed = Signal()
    entry_added = Signal(object)  # TimelineEntry
    preview = Signal(object)  # JPEG bytes
    finished = Signal(object)  # RunSummary
    started = Signal()  # a new run begins; views clear their old content

    def __init__(
        self,
        runs: AutomationRunService,
        prepare_demo: Callable[[], int] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runs = runs
        self._prepare_demo = prepare_demo
        self._observer = QtRunObserver(self)
        self._connect()
        self.entries: deque[TimelineEntry] = deque(maxlen=MAX_EVENTS)
        self.summary = RunSummary()
        self.phase = RunPhase.IDLE
        self._reset()

    # -- model ------------------------------------------------------------------- #
    def _reset(self) -> None:
        self.last_session_id: int | None = None
        self.scenario_name = ""
        self.keyword = ""
        self.scenario_index = 0
        self.scenario_total = 0
        self.iteration = 0
        self.total_iterations = 0
        self.state: SessionState | None = None
        self.step = ""
        self.url = ""
        self.target_position: int | None = None
        self.target_resolved = False
        self.dwell_remaining: float | None = None
        self.dwell_total: float | None = None
        self.browser_name = ""
        self.proxy_name = ""
        self.done_sessions = 0
        self.total_sessions = 0
        self.waiting_seconds: float | None = None
        self._elapsed_base = 0.0
        self._running_since: float | None = None
        self._last_active_state: SessionState | None = None

    @property
    def is_active(self) -> bool:
        return self.phase is not RunPhase.IDLE

    @property
    def has_run(self) -> bool:
        """Whether anything was run since the application started."""
        return self.total_sessions > 0

    @property
    def can_pause(self) -> bool:
        return self.phase is RunPhase.RUNNING

    @property
    def can_resume(self) -> bool:
        return self.phase is RunPhase.PAUSED

    @property
    def can_stop(self) -> bool:
        return self.phase in {RunPhase.RUNNING, RunPhase.PAUSED}

    def session_elapsed(self) -> float:
        """Seconds the current session has been active (frozen while paused)."""
        running = 0.0 if self._running_since is None else time.monotonic() - self._running_since
        return self._elapsed_base + running

    def dwell_fraction(self) -> float:
        if not self.dwell_total or self.dwell_remaining is None:
            return 0.0
        return 1.0 - self.dwell_remaining / self.dwell_total

    def progress(self) -> float:
        """Overall progress of the run, 0..1."""
        if self.total_sessions == 0:
            return 0.0
        current = 0.0
        if self.phase is not RunPhase.IDLE and self.done_sessions < self.total_sessions:
            current = session_progress(self._last_active_state, self.dwell_fraction())
        return min((self.done_sessions + current) / self.total_sessions, 1.0)

    # -- commands ------------------------------------------------------------------ #
    def start_plans(self, plans: list[SessionPlan]) -> None:
        """Start a run from prepared plans (Google Research site tests)."""
        if self.is_active:
            raise AutomationError("A run is already in progress.")
        self.entries.clear()
        self.summary = RunSummary()
        self._reset()
        self.phase = RunPhase.RUNNING
        self.started.emit()
        try:
            self._runs.start_plans(plans, self._observer)
        except Exception:
            self.phase = RunPhase.IDLE
            raise
        self.changed.emit()

    def start_demo(self) -> None:
        """Run the bundled demo scenario once (repairing the demo data first if needed)."""
        if self._prepare_demo is None:
            raise AutomationError("Demo mode is not available.")
        if self.is_active:
            raise AutomationError("A run is already in progress.")
        self.start([self._prepare_demo()], iterations_override=1)

    def start(self, scenario_ids: list[int], iterations_override: int | None = None) -> None:
        """Start a run. Raises :class:`AutomationError` if it cannot start."""
        if self.is_active:
            raise AutomationError("A run is already in progress.")
        if not scenario_ids:
            raise AutomationError("Select at least one scenario to run.")
        self.entries.clear()
        self.summary = RunSummary()
        self._reset()
        self.phase = RunPhase.RUNNING
        self.started.emit()
        try:
            self._runs.start(scenario_ids, self._observer, iterations_override)
        except Exception:
            self.phase = RunPhase.IDLE
            raise
        self.changed.emit()

    def pause(self) -> None:
        if self.can_pause:
            self._runs.pause()
            self.phase = RunPhase.PAUSED
            self.changed.emit()

    def resume(self) -> None:
        if self.can_resume:
            self._runs.resume()
            self.phase = RunPhase.RUNNING
            self.changed.emit()

    def stop(self) -> None:
        if self.can_stop:
            self._runs.stop()
            self.phase = RunPhase.STOPPING
            self.changed.emit()

    # -- signal handlers (GUI thread) ------------------------------------------------- #
    def _connect(self) -> None:
        obs = self._observer
        obs.run_started.connect(self._on_run_started)
        obs.scenario_started.connect(self._on_scenario_started)
        obs.session_started.connect(self._on_session_started)
        obs.state_changed.connect(self._on_state_changed)
        obs.event_logged.connect(self._on_event)
        obs.page_changed.connect(self._on_page_changed)
        obs.target_position.connect(self._on_target_position)
        obs.dwell_tick.connect(self._on_dwell_tick)
        obs.preview.connect(self.preview)
        obs.waiting.connect(self._on_waiting)
        obs.session_finished.connect(self._on_session_finished)
        obs.run_finished.connect(self._on_run_finished)

    def _on_run_started(self, plans: list[SessionPlan]) -> None:
        self.total_sessions = sum(plan.iterations for plan in plans)
        self.scenario_total = len(plans)
        self.changed.emit()

    def _on_scenario_started(self, plan: SessionPlan, index: int, total: int) -> None:
        self.scenario_name, self.keyword = plan.scenario_name, plan.keyword
        self.scenario_index, self.scenario_total = index, total
        self.total_iterations = plan.iterations
        self.browser_name = plan.browser.name
        self.proxy_name = plan.proxy.name if plan.proxy else NO_PROXY_LABEL
        self.changed.emit()

    def _on_session_started(self, session_id: int, iteration: int, total: int) -> None:
        self.iteration, self.total_iterations = iteration, total
        self.last_session_id = session_id
        self.state, self.step, self.url = SessionState.INITIALIZING, "", ""
        self.target_position, self.target_resolved = None, False
        self.dwell_remaining = self.dwell_total = None
        self.waiting_seconds = None
        self._last_active_state = None
        self._elapsed_base, self._running_since = 0.0, time.monotonic()
        self.changed.emit()

    def _on_state_changed(self, state: SessionState, message: str) -> None:
        was_paused = self.state is SessionState.PAUSED
        self.state, self.step = state, message or STATE_LABELS.get(state, state.value)
        if state is SessionState.PAUSED:
            self._freeze_clock()
        else:
            if was_paused or self._running_since is None:
                self._running_since = time.monotonic()
            if state not in _FINAL_STATES:
                self._last_active_state = state
            else:
                self._freeze_clock()
        self.changed.emit()

    def _freeze_clock(self) -> None:
        if self._running_since is not None:
            self._elapsed_base += time.monotonic() - self._running_since
            self._running_since = None

    def _on_event(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        entry = TimelineEntry(
            datetime.now().strftime("%H:%M:%S"), event_type, message, event_tone(event_type)
        )
        self.entries.append(entry)
        self.entry_added.emit(entry)

    def _on_page_changed(self, url: str) -> None:
        self.url = url
        self.changed.emit()

    def _on_target_position(self, position: int | None) -> None:
        self.target_position, self.target_resolved = position, True
        self.changed.emit()

    def _on_dwell_tick(self, remaining: float, planned: float) -> None:
        self.dwell_remaining, self.dwell_total = remaining, planned
        self.changed.emit()

    def _on_waiting(self, seconds: float) -> None:
        self.waiting_seconds = seconds
        self.changed.emit()

    def _on_session_finished(self, result: SessionResult) -> None:
        self.done_sessions += 1
        self.summary.add(result)
        self.state = result.state
        self.step = STATE_LABELS.get(result.state, result.state.value)
        if result.status is SessionStatus.TARGET_NOT_FOUND:
            self.step = "Completed: target not found"
        self._freeze_clock()
        self.changed.emit()

    def _on_run_finished(self, summary: RunSummary) -> None:
        self.phase = RunPhase.IDLE
        self.waiting_seconds = None
        self.changed.emit()
        self.finished.emit(summary)
