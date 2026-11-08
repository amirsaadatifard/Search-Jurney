"""Repository for automation sessions, their events and screenshots."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from src.database.models.automation_session import AutomationSession, Screenshot, SessionEvent
from src.database.repositories.base import Repository, like_pattern
from src.domain.enums import EventType, SessionState, SessionStatus


@dataclass(frozen=True, slots=True)
class SessionFilter:
    """Criteria shared by the session history and the reports.

    ``started_from`` is inclusive, ``started_until`` exclusive (naive UTC).
    """

    started_from: datetime | None = None
    started_until: datetime | None = None
    scenario_id: int | None = None
    keyword: str = ""
    target_domain: str = ""
    text: str = ""  # free text: matches scenario name, keyword or target domain
    status: SessionStatus | None = None

    def apply(self, statement: Select[Any]) -> Select[Any]:
        """Add the WHERE clauses for every criterion that is set."""
        if self.started_from is not None:
            statement = statement.where(AutomationSession.started_at >= self.started_from)
        if self.started_until is not None:
            statement = statement.where(AutomationSession.started_at < self.started_until)
        if self.scenario_id is not None:
            statement = statement.where(AutomationSession.scenario_id == self.scenario_id)
        if self.keyword.strip():
            statement = statement.where(
                AutomationSession.keyword.ilike(like_pattern(self.keyword.strip()), escape="\\")
            )
        if self.target_domain.strip():
            statement = statement.where(
                AutomationSession.target_domain.ilike(
                    like_pattern(self.target_domain.strip()), escape="\\"
                )
            )
        if self.text.strip():
            pattern = like_pattern(self.text.strip())
            statement = statement.where(
                AutomationSession.scenario_name.ilike(pattern, escape="\\")
                | AutomationSession.keyword.ilike(pattern, escape="\\")
                | AutomationSession.target_domain.ilike(pattern, escape="\\")
            )
        if self.status is not None:
            statement = statement.where(AutomationSession.status == self.status)
        return statement


class SessionRepository(Repository[AutomationSession]):
    model = AutomationSession

    def get_with_details(self, session_id: int) -> AutomationSession | None:
        """Load a session together with its events and screenshots."""
        statement = (
            select(AutomationSession)
            .where(AutomationSession.id == session_id)
            .options(
                selectinload(AutomationSession.events), selectinload(AutomationSession.screenshots)
            )
        )
        return self._session.scalars(statement).first()

    def search(
        self,
        criteria: SessionFilter | None = None,
        limit: int | None = None,
        offset: int = 0,
        with_details: bool = False,
    ) -> Sequence[AutomationSession]:
        """Return matching sessions, newest first."""
        statement = (criteria or SessionFilter()).apply(select(AutomationSession))
        statement = statement.order_by(
            AutomationSession.started_at.desc(), AutomationSession.id.desc()
        )
        if with_details:
            statement = statement.options(
                selectinload(AutomationSession.events), selectinload(AutomationSession.screenshots)
            )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        return self._session.scalars(statement).all()

    def count_matching(self, criteria: SessionFilter | None = None) -> int:
        statement = (criteria or SessionFilter()).apply(
            select(func.count()).select_from(AutomationSession)
        )
        return self._session.scalar(statement) or 0

    def latest(self) -> AutomationSession | None:
        statement = select(AutomationSession).order_by(AutomationSession.id.desc()).limit(1)
        return self._session.scalars(statement).first()

    def distinct_keywords(self) -> list[str]:
        statement = select(AutomationSession.keyword).distinct().order_by(AutomationSession.keyword)
        return list(self._session.scalars(statement).all())

    def distinct_target_domains(self) -> list[str]:
        statement = (
            select(AutomationSession.target_domain)
            .distinct()
            .order_by(AutomationSession.target_domain)
        )
        return list(self._session.scalars(statement).all())

    def add_event(
        self,
        session_id: int,
        event_type: EventType,
        message: str = "",
        state: SessionState | None = None,
        data: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> SessionEvent:
        """Append an entry to the session's timeline."""
        event = SessionEvent(
            session_id=session_id,
            event_type=event_type,
            message=message,
            state=state,
            data=data or {},
        )
        if timestamp is not None:
            event.timestamp = timestamp
        self._session.add(event)
        self._session.flush()
        return event

    def add_screenshot(self, screenshot: Screenshot) -> Screenshot:
        self._session.add(screenshot)
        self._session.flush()
        return screenshot

    def mark_interrupted_sessions(self, finished_at: datetime) -> int:
        """Close sessions left ``RUNNING`` by a crash or forced exit.

        Returns the number of sessions that were corrected.
        """
        statement = select(AutomationSession).where(
            AutomationSession.status == SessionStatus.RUNNING
        )
        interrupted = self._session.scalars(statement).all()
        for session in interrupted:
            session.status = SessionStatus.FAILED
            session.state = SessionState.FAILED
            session.finished_at = finished_at
            session.duration_seconds = (finished_at - session.started_at).total_seconds()
            session.error_type = "InterruptedSession"
            session.error_message = "The application closed before this session finished."
        self._session.flush()
        return len(interrupted)
