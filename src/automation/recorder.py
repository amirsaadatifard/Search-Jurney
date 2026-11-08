"""Persists a session and its event timeline to the database."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.automation.models import SessionPlan
from src.database.base import utcnow
from src.database.models import AutomationSession, Screenshot
from src.database.repositories import SessionRepository
from src.database.session import Database
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus


class SessionRecorder:
    """Short transactions, one per call, so a crash loses at most the last event."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        plan: SessionPlan,
        run_id: str,
        iteration: int,
        seed: int,
        snapshot_extra: dict[str, Any],
    ) -> int:
        """Insert the ``RUNNING`` session row; returns its id."""
        with self._database.session_scope() as session:
            record = SessionRepository(session).add(
                AutomationSession(
                    run_id=run_id,
                    scenario_id=plan.scenario_id,
                    observation_id=plan.observation_id,
                    scenario_name=plan.scenario_name,
                    keyword=plan.keyword,
                    target_domain=plan.target_domain,
                    target_url=plan.target_url,
                    iteration=iteration,
                    total_iterations=plan.iterations,
                    status=SessionStatus.RUNNING,
                    state=SessionState.INITIALIZING,
                    browser_profile_name=plan.browser.name,
                    proxy_profile_name=plan.proxy.name if plan.proxy else "",
                    settings_snapshot={**plan.snapshot(), "seed": seed, **snapshot_extra},
                )
            )
            return record.id

    def add_event(
        self,
        session_id: int,
        event_type: EventType,
        message: str = "",
        state: SessionState | None = None,
        data: dict[str, Any] | None = None,
    ) -> datetime:
        with self._database.session_scope() as session:
            event = SessionRepository(session).add_event(
                session_id, event_type, message, state, data
            )
            return event.timestamp

    def set_state(self, session_id: int, state: SessionState) -> None:
        self.update(session_id, state=state)

    def update(self, session_id: int, **fields: Any) -> None:
        """Set columns of the session row (``browser_info``, ``target_position`` …)."""
        with self._database.session_scope() as session:
            record = SessionRepository(session).require(session_id)
            for name, value in fields.items():
                setattr(record, name, value)

    def merge_snapshot(self, session_id: int, extra: dict[str, Any]) -> None:
        with self._database.session_scope() as session:
            record = SessionRepository(session).require(session_id)
            record.settings_snapshot = {**record.settings_snapshot, **extra}

    def finish(
        self,
        session_id: int,
        status: SessionStatus,
        state: SessionState,
        error_type: str | None = None,
        error_message: str | None = None,
        **fields: Any,
    ) -> float:
        """Close the session; returns its duration in seconds."""
        finished = utcnow()
        with self._database.session_scope() as session:
            record = SessionRepository(session).require(session_id)
            record.status = status
            record.state = state
            record.finished_at = finished
            record.duration_seconds = round((finished - record.started_at).total_seconds(), 3)
            record.error_type = error_type
            record.error_message = error_message
            for name, value in fields.items():
                setattr(record, name, value)
            return record.duration_seconds

    def add_screenshot(
        self,
        session_id: int,
        kind: ScreenshotKind,
        file_path: str,
        page_url: str,
        width: int | None,
        height: int | None,
        size_bytes: int | None,
    ) -> int:
        with self._database.session_scope() as session:
            shot = SessionRepository(session).add_screenshot(
                Screenshot(
                    session_id=session_id,
                    kind=kind,
                    file_path=file_path,
                    page_url=page_url[:500],
                    width=width,
                    height=height,
                    size_bytes=size_bytes,
                )
            )
            return shot.id
