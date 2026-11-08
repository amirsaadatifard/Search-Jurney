"""Report calculations, exporters and the session service."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.app.paths import AppPaths
from src.database.models import Screenshot
from src.database.repositories import SessionRepository
from src.database.session import Database
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus
from src.domain.exceptions import AppError, EntityNotFoundError, InvalidConfigurationError
from src.reports import export_csv, export_excel, export_json
from src.reports.common import neutralise_formulas
from src.services.report_service import ReportFilter, ReportService, compute_metrics
from src.services.session_service import SessionService
from src.services.timeutil import local_day_start_utc
from tests.factories import make_session

NOON = timedelta(hours=12)


def at(day: date, hours: float = 0) -> datetime:
    """A naive UTC instant that falls on local calendar ``day`` (noon plus ``hours``)."""
    return local_day_start_utc(day) + NOON + timedelta(hours=hours)


DAY1, DAY2, DAY3 = date(2026, 3, 10), date(2026, 3, 11), date(2026, 3, 12)


@pytest.fixture
def seeded(database: Database) -> Database:
    """Seven sessions with known numbers (see ``test_metrics``)."""
    with database.session_scope() as session:
        repo = SessionRepository(session)

        def add(**kwargs: object) -> int:
            return repo.add(make_session(**kwargs)).id  # type: ignore[arg-type]

        for position, dwell, duration, offset in (
            (2, 10.0, 30.0, 0),
            (4, 20.0, 40.0, 1),
            (6, 30.0, 50.0, 2),
        ):
            add(
                started_at=at(DAY1, offset),
                duration=duration,
                target_position=position,
                actual_dwell_seconds=dwell,
            )
        failed = add(
            started_at=at(DAY2),
            status=SessionStatus.FAILED,
            duration=5.0,
            keyword="silver chain",
            error_type="NavigationError",
            error_message="page did not load",
            scenario_name="Second scenario",
        )
        add(
            started_at=at(DAY2, 1),
            status=SessionStatus.TARGET_NOT_FOUND,
            duration=12.0,
            target_domain="staging.example.test",
        )
        add(started_at=at(DAY3), status=SessionStatus.STOPPED, duration=3.0)
        add(
            started_at=at(DAY3, 1),
            status=SessionStatus.RUNNING,
            finished_at=None,
            duration_seconds=None,
        )
        repo.add_event(failed, EventType.SESSION_STARTED, "start", SessionState.INITIALIZING)
        repo.add_event(
            failed,
            EventType.ERROR,
            "page did not load",
            SessionState.OPENING_SEARCH,
            {"type": "NavigationError"},
        )
        repo.add_event(
            failed, EventType.SAFETY_BLOCKED, "blocked https://x.test", SessionState.OPENING_TARGET
        )
        repo.add_event(failed, EventType.SESSION_FAILED, "page did not load", SessionState.FAILED)
    return database


# -- metrics --------------------------------------------------------------------- #
def test_metrics(seeded: Database) -> None:
    metrics = ReportService(seeded).build().metrics
    assert (metrics.sessions, metrics.successful, metrics.failed) == (7, 3, 1)
    assert (metrics.target_not_found, metrics.stopped, metrics.running) == (1, 1, 1)
    assert metrics.success_rate == pytest.approx(3 / 5)  # stopped and running do not count
    assert metrics.average_dwell_seconds == pytest.approx(20.0)
    assert metrics.average_target_position == pytest.approx(4.0)
    # 30, 40, 50, 5, 12, 3 (the running session has no duration yet)
    assert metrics.average_session_duration_seconds == pytest.approx(
        (30 + 40 + 50 + 5 + 12 + 3) / 6
    )


def test_metrics_of_nothing_are_empty_not_zero_division() -> None:
    metrics = compute_metrics([])
    assert metrics.sessions == 0 and metrics.success_rate is None
    assert metrics.average_dwell_seconds is None and metrics.average_target_position is None
    assert dict(metrics.as_rows())["Success rate"] == "n/a"


def test_metrics_formatting() -> None:
    rows = dict(compute_metrics([]).as_rows())
    assert rows["Sessions"] == "0" and rows["Average dwell time"] == "n/a"


# -- filters ----------------------------------------------------------------------- #
def test_date_range_is_inclusive_on_local_days(seeded: Database) -> None:
    service = ReportService(seeded)
    assert service.build(ReportFilter(DAY1, DAY1)).metrics.sessions == 3
    assert service.build(ReportFilter(DAY2, DAY2)).metrics.sessions == 2
    assert service.build(ReportFilter(DAY1, DAY2)).metrics.sessions == 5
    assert service.build(ReportFilter(DAY2, None)).metrics.sessions == 4
    assert service.build(ReportFilter(None, DAY1)).metrics.sessions == 3
    assert service.build(ReportFilter(date(2030, 1, 1), None)).metrics.sessions == 0


def test_other_filters(seeded: Database) -> None:
    service = ReportService(seeded)
    assert service.build(ReportFilter(status=SessionStatus.FAILED)).metrics.sessions == 1
    assert service.build(ReportFilter(keyword="silver")).metrics.failed == 1
    assert service.build(ReportFilter(target_domain="staging")).metrics.target_not_found == 1
    combined = service.build(ReportFilter(DAY1, DAY2, status=SessionStatus.COMPLETED))
    assert combined.metrics.sessions == 3
    assert service.build(ReportFilter(scenario_id=987)).metrics.sessions == 0


def test_filter_choices(seeded: Database) -> None:
    choices = ReportService(seeded).filter_choices()
    assert choices["keywords"] == ["gold ring", "silver chain"]
    assert choices["domains"] == ["127.0.0.1", "staging.example.test"]


# -- tables ------------------------------------------------------------------------- #
def test_frames_have_stable_columns_and_content(seeded: Database) -> None:
    report = ReportService(seeded).build()
    assert len(report.sessions) == 7 and list(report.sessions.columns)[:3] == [
        "id",
        "run_id",
        "scenario",
    ]
    failed = report.sessions[report.sessions["status"] == "FAILED"].iloc[0]
    assert (failed["error_type"], failed["keyword"]) == ("NavigationError", "silver chain")
    assert failed["proxy_profile"] == "direct"
    assert pd.api.types.is_datetime64_any_dtype(report.sessions["started_at"])
    assert len(report.events) == 4 and set(report.events["session_id"]) == {int(failed["id"])}
    assert list(report.errors["event_type"]) == ["ERROR", "SAFETY_BLOCKED"]
    assert report.errors["scenario"].iloc[0] == "Second scenario"


def test_empty_report_still_has_all_columns(database: Database) -> None:
    report = ReportService(database).build()
    assert report.sessions.empty and "target_position" in report.sessions.columns
    assert report.errors.columns[0] == "session_id"
    assert report.to_dict()["sessions"] == []


# -- exporters ----------------------------------------------------------------------- #
def test_json_export(seeded: Database, tmp_path: Path) -> None:
    report = ReportService(seeded).build(ReportFilter(DAY2, DAY2))
    path = export_json(report, tmp_path / "report")
    assert path.name == "report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["metrics"]["sessions"] == 2 and data["filter"]["date_from"] == DAY2.isoformat()
    assert len(data["sessions"]) == 2 and data["sessions"][0]["error_message"] in {
        None,
        "page did not load",
    }
    assert any(e["event_type"] == "ERROR" for e in data["events"])
    assert isinstance(data["sessions"][0]["started_at"], str)  # ISO text, not a number
    assert data["sessions"][0]["finished_at"] is not None


def test_csv_export_and_formula_injection(database: Database, tmp_path: Path) -> None:
    with database.session_scope() as session:
        SessionRepository(session).add(make_session(keyword='=HYPERLINK("http://evil.test")'))
        SessionRepository(session).add(make_session(keyword="plain, with comma"))
    path = export_csv(ReportService(database).build(), tmp_path / "sessions.csv")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM so Excel detects UTF-8
    frame = pd.read_csv(path, encoding="utf-8-sig")
    assert len(frame) == 2
    assert "'=HYPERLINK" in " ".join(frame["keyword"])  # neutralised
    assert "plain, with comma" in set(frame["keyword"])  # quoting preserved the comma


def test_excel_export_has_the_four_sheets(seeded: Database, tmp_path: Path) -> None:
    report = ReportService(seeded).build()
    path = export_excel(report, tmp_path / "report.xlsx")
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Summary", "Sessions", "Events", "Errors"]

    summary = {row[0].value: row[1].value for row in workbook["Summary"].iter_rows(min_row=2)}
    assert summary["Sessions"] == "7" and summary["Success rate"] == "60.0 %"
    assert summary["Status"] == "any" and summary["Average target position"] == "4.00"

    sessions = workbook["Sessions"]
    assert sessions.max_row == 8 and sessions["A1"].value == "id"
    assert sessions.freeze_panes == "A2" and sessions.auto_filter.ref
    assert workbook["Events"].max_row == 5 and workbook["Errors"].max_row == 3
    header = [cell.value for cell in workbook["Errors"][1]]
    assert header[:3] == ["session_id", "scenario", "keyword"]


def test_excel_neutralises_formulas(database: Database, tmp_path: Path) -> None:
    with database.session_scope() as session:
        SessionRepository(session).add(make_session(keyword="=1+1", error_message="@SUM(A1)"))
    workbook = load_workbook(export_excel(ReportService(database).build(), tmp_path / "x.xlsx"))
    values = [cell.value for row in workbook["Sessions"].iter_rows(min_row=2) for cell in row]
    assert "'=1+1" in values and "'@SUM(A1)" in values
    assert "=1+1" not in values


def test_export_path_validation(seeded: Database, tmp_path: Path) -> None:
    report = ReportService(seeded).build()
    with pytest.raises(InvalidConfigurationError, match="does not exist"):
        export_csv(report, tmp_path / "missing" / "x.csv")
    (tmp_path / "folder.csv").mkdir()
    with pytest.raises(InvalidConfigurationError, match="folder"):
        export_csv(report, tmp_path / "folder.csv")
    assert export_json(report, tmp_path / "a.txt").suffix == ".json"  # wrong extension corrected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("=A1", "'=A1"),
        ("+1", "'+1"),
        ("-2", "'-2"),
        ("@x", "'@x"),
        ("ok", "ok"),
        (5, 5),
        (None, None),
    ],
)
def test_neutralise_formulas(raw: object, expected: object) -> None:
    assert neutralise_formulas(raw) == expected


# -- session service ---------------------------------------------------------------------- #
def test_session_service_search_details_and_delete(seeded: Database, app_paths: AppPaths) -> None:
    service = SessionService(seeded, app_paths)
    assert len(service.search()) == 7
    assert [s.status for s in service.search(status=SessionStatus.FAILED)] == [SessionStatus.FAILED]
    assert len(service.search(text="second")) == 1 and len(service.search(text="staging")) == 1
    assert len(service.search(limit=3)) == 3

    failed = service.search(status=SessionStatus.FAILED)[0]
    details = service.get_details(failed.id)
    assert [e.event_type for e in details.events][1] is EventType.ERROR
    with pytest.raises(EntityNotFoundError):
        service.get_details(9999)

    running = service.search(status=SessionStatus.RUNNING)[0]
    with pytest.raises(AppError, match="running session"):
        service.delete([running.id])
    assert service.delete([failed.id, 9999]) == 1
    assert len(service.search()) == 6


def test_deleting_a_session_removes_only_its_own_screenshots(
    database: Database, app_paths: AppPaths, tmp_path: Path
) -> None:
    inside = app_paths.screenshots_dir / "2026-03-10" / "shot.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"png")
    outside = tmp_path / "precious.txt"
    outside.write_text("do not delete")
    with database.session_scope() as session:
        repo = SessionRepository(session)
        record = repo.add(make_session())
        for path in (inside, outside):
            repo.add_screenshot(
                Screenshot(
                    session_id=record.id, kind=ScreenshotKind.SESSION_END, file_path=str(path)
                )
            )
        session_id = record.id
    assert SessionService(database, app_paths).delete([session_id]) == 1
    assert not inside.exists()
    assert outside.read_text() == "do not delete"  # outside the screenshots folder: untouched
