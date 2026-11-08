"""Statistics for the dashboard."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final

from src.database.models import AutomationSession
from src.database.repositories import ScenarioRepository, SessionRepository
from src.database.session import Database
from src.domain.enums import SessionStatus
from src.services.report_service import compute_metrics
from src.services.timeutil import to_local

DEFAULT_DAYS: Final = 14


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """Aggregates of all sessions that started on one local calendar day."""

    day: date
    sessions: int = 0
    average_position: float | None = None
    average_dwell_seconds: float | None = None


@dataclass(slots=True)
class DashboardStats:
    total_scenarios: int = 0
    enabled_scenarios: int = 0
    total_sessions: int = 0
    successful: int = 0
    failed: int = 0
    target_not_found: int = 0
    stopped: int = 0
    success_rate: float | None = None
    average_dwell_seconds: float | None = None
    average_target_position: float | None = None
    sessions_today: int = 0
    last_execution: datetime | None = None
    last_scenario: str = ""
    daily: list[DailyPoint] = field(default_factory=list)

    def outcomes(self) -> list[tuple[SessionStatus, int]]:
        """Finished-session outcomes in display order."""
        return [
            (SessionStatus.COMPLETED, self.successful),
            (SessionStatus.TARGET_NOT_FOUND, self.target_not_found),
            (SessionStatus.FAILED, self.failed),
            (SessionStatus.STOPPED, self.stopped),
        ]


def daily_points(
    sessions: list[AutomationSession], today: date, days: int = DEFAULT_DAYS
) -> list[DailyPoint]:
    """One point per local day for the last ``days`` days, ending today (oldest first)."""
    buckets: dict[date, list[AutomationSession]] = defaultdict(list)
    for record in sessions:
        buckets[to_local(record.started_at).date()].append(record)

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    points: list[DailyPoint] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        rows = buckets.get(day, [])
        points.append(
            DailyPoint(
                day=day,
                sessions=len(rows),
                average_position=mean([r.target_position for r in rows if r.target_position]),
                average_dwell_seconds=mean(
                    [r.actual_dwell_seconds for r in rows if r.actual_dwell_seconds is not None]
                ),
            )
        )
    return points


class DashboardService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def load(self, days: int = DEFAULT_DAYS, today: date | None = None) -> DashboardStats:
        """Compute every number the dashboard shows. Safe to call off the GUI thread."""
        today = today or datetime.now().astimezone().date()
        with self._database.session_scope() as session:
            scenarios = ScenarioRepository(session)
            total, enabled = scenarios.count(), scenarios.count_enabled()
            sessions = list(SessionRepository(session).search())  # newest first

        metrics = compute_metrics(sessions)
        stats = DashboardStats(
            total_scenarios=total,
            enabled_scenarios=enabled,
            total_sessions=metrics.sessions,
            successful=metrics.successful,
            failed=metrics.failed,
            target_not_found=metrics.target_not_found,
            stopped=metrics.stopped,
            success_rate=metrics.success_rate,
            average_dwell_seconds=metrics.average_dwell_seconds,
            average_target_position=metrics.average_target_position,
            sessions_today=sum(1 for s in sessions if to_local(s.started_at).date() == today),
            daily=daily_points(sessions, today, days),
        )
        if sessions:
            stats.last_execution = sessions[0].started_at
            stats.last_scenario = sessions[0].scenario_name
        return stats
