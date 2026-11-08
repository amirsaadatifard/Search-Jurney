"""Callbacks through which the GUI follows a run.

Methods are invoked on the automation thread. GUI code must hand the data over
to the Qt main thread (signals) instead of touching widgets directly. An
observer that raises never affects the run; see :class:`SafeObserver`.
"""

from __future__ import annotations

from typing import Any

from src.automation.models import RunSummary, SessionPlan, SessionResult
from src.domain.enums import SessionState
from src.logging_config import get_logger

log = get_logger(__name__)


class RunObserver:
    """Receives progress notifications. Override what you need."""

    def on_run_started(self, plans: list[SessionPlan]) -> None: ...

    def on_scenario_started(self, plan: SessionPlan, index: int, total: int) -> None: ...

    def on_session_started(self, session_id: int, iteration: int, total: int) -> None: ...

    def on_state_changed(self, state: SessionState, message: str) -> None: ...

    def on_event(self, event_type: str, message: str, data: dict[str, Any]) -> None: ...

    def on_page_changed(self, url: str) -> None: ...

    def on_target_position(self, position: int | None) -> None: ...

    def on_dwell_tick(self, remaining_seconds: float, planned_seconds: float) -> None: ...

    def on_preview(self, jpeg: bytes) -> None:
        """A small screenshot of the page the session is on."""

    def on_waiting(self, seconds: float) -> None:
        """Pause between two iterations."""

    def on_session_finished(self, result: SessionResult) -> None: ...

    def on_run_finished(self, summary: RunSummary) -> None: ...


class SafeObserver(RunObserver):
    """Forwards every call to ``inner`` and swallows (but logs) its exceptions."""

    def __init__(self, inner: RunObserver | None) -> None:
        self._inner = inner

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("on_"):
            inner = object.__getattribute__(self, "_inner")
            target = getattr(inner, name, None) if inner is not None else None

            def call(*args: Any, **kwargs: Any) -> None:
                if target is None:
                    return
                try:
                    target(*args, **kwargs)
                except Exception:
                    log.exception("observer_failed", callback=name)

            return call
        return object.__getattribute__(self, name)
