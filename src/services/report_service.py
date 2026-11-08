"""Report calculations: filters, metrics and tabular data for the exporters.

Definitions (also shown to users in the documentation):

* **successful** - sessions that ended ``COMPLETED`` (the target was found and visited);
* **failed** - sessions that ended ``FAILED``;
* **target not found** - the run worked, but the target was not in the results;
* **success rate** - ``successful / (successful + failed + target not found)``. Sessions
  the user stopped and sessions still running are not counted either way;
* averages ignore sessions that have no value (e.g. no dwell when the target was not found).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Final

import pandas as pd

from src.database.models import AutomationSession
from src.database.repositories import SessionFilter, SessionRepository
from src.database.session import Database
from src.domain.enums import EventType, SessionStatus
from src.services.timeutil import local_day_range_utc, to_local_naive

ERROR_EVENT_TYPES: Final = frozenset({EventType.ERROR, EventType.SAFETY_BLOCKED})

SESSION_COLUMNS: Final = (
    "id",
    "run_id",
    "scenario",
    "keyword",
    "target_domain",
    "target_url",
    "iteration",
    "status",
    "state",
    "started_at",
    "finished_at",
    "duration_seconds",
    "target_found",
    "target_position",
    "results_count",
    "planned_dwell_seconds",
    "actual_dwell_seconds",
    "scroll_actions",
    "browser_profile",
    "proxy_profile",
    "error_type",
    "error_message",
)
EVENT_COLUMNS: Final = ("session_id", "timestamp", "event_type", "state", "message", "data")
ERROR_COLUMNS: Final = (
    "session_id",
    "scenario",
    "keyword",
    "timestamp",
    "event_type",
    "message",
    "session_status",
    "session_error_type",
)


@dataclass(frozen=True, slots=True)
class ReportFilter:
    """Criteria chosen on the Reports page. Dates are local calendar days, inclusive."""

    date_from: date | None = None
    date_to: date | None = None
    scenario_id: int | None = None
    keyword: str = ""
    target_domain: str = ""
    status: SessionStatus | None = None

    def to_session_filter(self) -> SessionFilter:
        start, end = local_day_range_utc(self.date_from, self.date_to)
        return SessionFilter(
            started_from=start,
            started_until=end,
            scenario_id=self.scenario_id,
            keyword=self.keyword,
            target_domain=self.target_domain,
            status=self.status,
        )

    def describe(self) -> dict[str, str]:
        """Human-readable summary for the Summary sheet and JSON export."""
        return {
            "date_from": self.date_from.isoformat() if self.date_from else "any",
            "date_to": self.date_to.isoformat() if self.date_to else "any",
            "scenario_id": str(self.scenario_id) if self.scenario_id else "any",
            "keyword": self.keyword or "any",
            "target_domain": self.target_domain or "any",
            "status": self.status.value if self.status else "any",
        }


@dataclass(frozen=True, slots=True)
class ReportMetrics:
    sessions: int = 0
    successful: int = 0
    failed: int = 0
    target_not_found: int = 0
    stopped: int = 0
    running: int = 0
    success_rate: float | None = None
    average_dwell_seconds: float | None = None
    average_target_position: float | None = None
    average_session_duration_seconds: float | None = None

    def as_rows(self) -> list[tuple[str, str]]:
        """``(label, formatted value)`` pairs in display order."""

        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value * 100:.1f} %"

        def num(value: float | None, unit: str = "") -> str:
            return "n/a" if value is None else f"{value:.2f}{unit}"

        return [
            ("Sessions", str(self.sessions)),
            ("Successful", str(self.successful)),
            ("Failed", str(self.failed)),
            ("Success rate", pct(self.success_rate)),
            ("Target not found", str(self.target_not_found)),
            ("Stopped by user", str(self.stopped)),
            ("Average dwell time", num(self.average_dwell_seconds, " s")),
            ("Average target position", num(self.average_target_position)),
            ("Average session duration", num(self.average_session_duration_seconds, " s")),
        ]


@dataclass(slots=True)
class ReportData:
    """Everything a report contains."""

    filter: ReportFilter
    generated_at: datetime
    metrics: ReportMetrics
    sessions: pd.DataFrame
    events: pd.DataFrame
    errors: pd.DataFrame
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-compatible representation (used by the JSON exporter)."""
        return {
            "generated_at": self.generated_at.isoformat(timespec="seconds"),
            "filter": self.filter.describe(),
            "metrics": asdict(self.metrics),
            "sessions": _records(self.sessions),
            "events": _records(self.events),
            "errors": _records(self.errors),
        }


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Rows as plain dicts with timestamps as ISO strings and NaN as ``None``."""
    if frame.empty:
        return []
    text = frame.copy()
    for column in text.columns:
        if pd.api.types.is_datetime64_any_dtype(text[column]):
            text[column] = text[column].map(lambda v: None if pd.isna(v) else v.isoformat())
    cleaned = text.astype(object).where(text.notna(), None)
    return json.loads(json.dumps(cleaned.to_dict(orient="records"), default=str))


def compute_metrics(sessions: Iterable[AutomationSession]) -> ReportMetrics:
    """Aggregate the session rows according to the definitions in the module docstring."""
    rows = list(sessions)
    counts = dict.fromkeys(SessionStatus, 0)
    for row in rows:
        counts[row.status] += 1
    decided = (
        counts[SessionStatus.COMPLETED]
        + counts[SessionStatus.FAILED]
        + counts[SessionStatus.TARGET_NOT_FOUND]
    )

    def mean(values: Sequence[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return ReportMetrics(
        sessions=len(rows),
        successful=counts[SessionStatus.COMPLETED],
        failed=counts[SessionStatus.FAILED],
        target_not_found=counts[SessionStatus.TARGET_NOT_FOUND],
        stopped=counts[SessionStatus.STOPPED],
        running=counts[SessionStatus.RUNNING],
        success_rate=counts[SessionStatus.COMPLETED] / decided if decided else None,
        average_dwell_seconds=mean(
            [r.actual_dwell_seconds for r in rows if r.actual_dwell_seconds is not None]
        ),
        average_target_position=mean(
            [r.target_position for r in rows if r.target_position is not None]
        ),
        average_session_duration_seconds=mean(
            [r.duration_seconds for r in rows if r.duration_seconds is not None]
        ),
    )


def sessions_frame(rows: Sequence[AutomationSession]) -> pd.DataFrame:
    records = [
        {
            "id": r.id,
            "run_id": r.run_id,
            "scenario": r.scenario_name,
            "keyword": r.keyword,
            "target_domain": r.target_domain,
            "target_url": r.target_url,
            "iteration": r.iteration,
            "status": r.status.value,
            "state": r.state.value,
            "started_at": to_local_naive(r.started_at),
            "finished_at": to_local_naive(r.finished_at),
            "duration_seconds": r.duration_seconds,
            "target_found": r.target_found,
            "target_position": r.target_position,
            "results_count": r.results_count,
            "planned_dwell_seconds": r.planned_dwell_seconds,
            "actual_dwell_seconds": r.actual_dwell_seconds,
            "scroll_actions": r.scroll_actions,
            "browser_profile": r.browser_profile_name,
            "proxy_profile": r.proxy_profile_name or "direct",
            "error_type": r.error_type,
            "error_message": r.error_message,
        }
        for r in rows
    ]
    return pd.DataFrame(records, columns=list(SESSION_COLUMNS))


def events_frame(rows: Sequence[AutomationSession]) -> pd.DataFrame:
    records = [
        {
            "session_id": r.id,
            "timestamp": to_local_naive(e.timestamp),
            "event_type": e.event_type.value,
            "state": e.state.value if e.state else None,
            "message": e.message,
            "data": json.dumps(e.data, ensure_ascii=False, sort_keys=True) if e.data else "",
        }
        for r in rows
        for e in r.events
    ]
    return pd.DataFrame(records, columns=list(EVENT_COLUMNS))


def errors_frame(rows: Sequence[AutomationSession]) -> pd.DataFrame:
    records = [
        {
            "session_id": r.id,
            "scenario": r.scenario_name,
            "keyword": r.keyword,
            "timestamp": to_local_naive(e.timestamp),
            "event_type": e.event_type.value,
            "message": e.message,
            "session_status": r.status.value,
            "session_error_type": r.error_type,
        }
        for r in rows
        for e in r.events
        if e.event_type in ERROR_EVENT_TYPES
    ]
    return pd.DataFrame(records, columns=list(ERROR_COLUMNS))


class ReportService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def build(self, report_filter: ReportFilter | None = None) -> ReportData:
        """Query the sessions and assemble metrics and tables. Safe to run off the GUI thread."""
        report_filter = report_filter or ReportFilter()
        with self._database.session_scope() as session:
            rows = list(
                SessionRepository(session).search(
                    report_filter.to_session_filter(), with_details=True
                )
            )
        return ReportData(
            filter=report_filter,
            generated_at=datetime.now().astimezone().replace(tzinfo=None),
            metrics=compute_metrics(rows),
            sessions=sessions_frame(rows),
            events=events_frame(rows),
            errors=errors_frame(rows),
        )

    def filter_choices(self) -> dict[str, list[Any]]:
        """Values for the filter drop-downs."""
        with self._database.session_scope() as session:
            repo = SessionRepository(session)
            scenarios = sorted(
                {(r.scenario_id, r.scenario_name) for r in repo.search() if r.scenario_id},
                key=lambda pair: pair[1].lower(),
            )
            return {
                "scenarios": scenarios,
                "keywords": repo.distinct_keywords(),
                "domains": repo.distinct_target_domains(),
            }


__all__ = [
    "ReportData",
    "ReportFilter",
    "ReportMetrics",
    "ReportService",
    "compute_metrics",
]
