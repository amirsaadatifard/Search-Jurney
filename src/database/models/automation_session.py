"""Executed automation sessions, their event timeline and screenshots."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base, enum_type, utcnow
from src.database.models.keyword import KEYWORD_MAX_LENGTH
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus

if TYPE_CHECKING:
    from src.database.models.scenario import Scenario


class AutomationSession(Base):
    """One iteration of a scenario.

    The descriptive columns (``scenario_name``, ``keyword``, ``target_domain`` …)
    and ``settings_snapshot`` are copied at start time, so history keeps its
    meaning even if the scenario is edited or deleted afterwards.
    """

    __tablename__ = "automation_sessions"
    __table_args__ = (Index("ix_automation_sessions_status_started", "status", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), index=True
    )
    scenario_name: Mapped[str] = mapped_column(String(120), nullable=False)
    keyword: Mapped[str] = mapped_column(String(KEYWORD_MAX_LENGTH), nullable=False, index=True)
    target_domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_url: Mapped[str] = mapped_column(String(500), nullable=False)
    observation_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_observations.id", ondelete="SET NULL"), index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total_iterations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    status: Mapped[SessionStatus] = mapped_column(
        enum_type(SessionStatus), default=SessionStatus.RUNNING, nullable=False
    )
    state: Mapped[SessionState] = mapped_column(
        enum_type(SessionState), default=SessionState.INITIALIZING, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    target_found: Mapped[bool | None] = mapped_column(Boolean)
    target_position: Mapped[int | None] = mapped_column(Integer)
    results_count: Mapped[int | None] = mapped_column(Integer)
    planned_dwell_seconds: Mapped[float | None] = mapped_column(Float)
    actual_dwell_seconds: Mapped[float | None] = mapped_column(Float)
    scroll_actions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    browser_profile_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    proxy_profile_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    browser_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    error_type: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)

    scenario: Mapped[Scenario | None] = relationship(back_populates="sessions")
    events: Mapped[list[SessionEvent]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SessionEvent.id",
    )
    screenshots: Mapped[list[Screenshot]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Screenshot.id",
    )

    def __repr__(self) -> str:
        return f"AutomationSession(id={self.id!r}, status={self.status.value!r})"


class SessionEvent(Base):
    """A timestamped entry in a session's timeline."""

    __tablename__ = "session_events"
    __table_args__ = (Index("ix_session_events_session_timestamp", "session_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("automation_sessions.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    event_type: Mapped[EventType] = mapped_column(enum_type(EventType), nullable=False, index=True)
    state: Mapped[SessionState | None] = mapped_column(enum_type(SessionState))
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    session: Mapped[AutomationSession] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"SessionEvent(session_id={self.session_id!r}, type={self.event_type.value!r})"


class Screenshot(Base):
    """Metadata of an image captured during a session (the file lives on disk)."""

    __tablename__ = "screenshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("automation_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ScreenshotKind] = mapped_column(enum_type(ScreenshotKind), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    page_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    session: Mapped[AutomationSession] = relationship(back_populates="screenshots")

    def __repr__(self) -> str:
        return f"Screenshot(session_id={self.session_id!r}, kind={self.kind.value!r})"
