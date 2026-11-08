"""Session history, session details, logs page, reports page and the background task runner."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from openpyxl import load_workbook
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.automation.models import RunSummary
from src.database.models import Screenshot
from src.database.repositories import SessionRepository
from src.domain.enums import EventType, ScreenshotKind, SessionState, SessionStatus
from src.domain.exceptions import AutomationError
from src.logging_config import get_logger
from src.ui.dialogs import session_dialog
from src.ui.dialogs.session_dialog import SessionDetailDialog, flatten
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.logs_page import LogsPage
from src.ui.pages.reports_page import ReportsPage, preview_rows
from src.ui.pages.sessions_page import SessionsPage
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.workers import UNEXPECTED_ERROR, TaskRunner
from tests.factories import make_session

NOW = datetime.utcnow().replace(microsecond=0)


@pytest.fixture
def window(container: AppContainer) -> Iterator[MainWindow]:
    app = cast(QApplication, QApplication.instance())
    main_window = MainWindow(container, ThemeManager(app, ThemeMode.LIGHT))
    main_window.show()
    yield main_window
    main_window.close()
    main_window.deleteLater()


@pytest.fixture
def seeded(container: AppContainer) -> AppContainer:
    """Four sessions; the failed one has events, a screenshot file and a settings snapshot."""
    shot = container.paths.screenshots_dir / "2026-03-10" / "session_2_keyword_silver_error.png"
    shot.parent.mkdir(parents=True)
    image = QImage(80, 50, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc5533"))
    assert image.save(str(shot))
    with container.database.session_scope() as session:
        repo = SessionRepository(session)
        repo.add(make_session(NOW - timedelta(hours=3), keyword="gold ring", target_position=3))
        failed = repo.add(
            make_session(
                NOW - timedelta(hours=2),
                SessionStatus.FAILED,
                keyword="silver chain",
                scenario_name="Second scenario",
                error_type="NavigationError",
                error_message="page did not load",
                browser_info={"browser": "chromium", "version": "153", "headless": True},
                settings_snapshot={
                    "keyword": "silver chain",
                    "browser": {"viewport_width": 1280},
                    "seed": 7,
                },
            )
        )
        repo.add(
            make_session(
                NOW - timedelta(hours=1),
                SessionStatus.TARGET_NOT_FOUND,
                target_domain="staging.example.test",
            )
        )
        repo.add(make_session(NOW, SessionStatus.STOPPED, keyword="gold ring"))
        repo.add_event(failed.id, EventType.SESSION_STARTED, "start", SessionState.INITIALIZING)
        repo.add_event(failed.id, EventType.ERROR, "page did not load", SessionState.OPENING_SEARCH)
        repo.add_screenshot(
            Screenshot(
                session_id=failed.id,
                kind=ScreenshotKind.ERROR,
                file_path=str(shot),
                page_url="http://x",
            )
        )
    return container


# -- background tasks ---------------------------------------------------------------- #
def test_task_runner_delivers_results_on_the_gui_thread(qtbot: QtBot) -> None:
    runner = TaskRunner()
    seen: dict[str, Any] = {}
    gui = threading.get_ident()

    def work() -> int:
        seen["worker"] = threading.get_ident()
        return 41 + 1

    def done(result: object) -> None:
        seen["result"], seen["callback"] = result, threading.get_ident()

    runner.submit(work, done, lambda message: seen.update(error=message))
    qtbot.waitUntil(lambda: "result" in seen)
    assert seen["result"] == 42 and seen["callback"] == gui and seen["worker"] != gui
    assert runner.pending == 0


def test_task_runner_reports_expected_and_unexpected_errors(qtbot: QtBot) -> None:
    runner = TaskRunner()
    errors: list[str] = []

    def expected() -> None:
        raise AutomationError("Something known went wrong.")

    def unexpected() -> None:
        raise ZeroDivisionError("bug")

    runner.submit(expected, lambda _r: None, errors.append)
    runner.submit(unexpected, lambda _r: None, errors.append)
    qtbot.waitUntil(lambda: len(errors) == 2)
    assert sorted(errors) == sorted(["Something known went wrong.", UNEXPECTED_ERROR])


# -- session history ----------------------------------------------------------------- #
def test_history_lists_newest_first_and_filters(window: MainWindow, seeded: AppContainer) -> None:
    window.show_page(PageKey.SESSIONS)
    page = cast(SessionsPage, window.page(PageKey.SESSIONS))
    assert page.table.row_count() == 4
    ids = [int(page.table.cell_text(row, 0)) for row in range(4)]
    assert ids == sorted(ids, reverse=True)
    assert page.table.cell_text(0, 9) == "Stopped" and page.footer.text() == "4 sessions"

    page.search_box.setText("silver")
    page.refresh()
    assert page.table.row_count() == 1 and page.table.cell_text(0, 1) == "Second scenario"
    assert page.table.cell_text(0, 9) == "Failed" and page.footer.text().endswith(
        "match the current filters"
    )
    page.search_box.setText("")
    page.status_filter.setCurrentIndex(page.status_filter.findData(SessionStatus.TARGET_NOT_FOUND))
    assert page.table.row_count() == 1 and page.table.cell_text(0, 3) == "staging.example.test"
    assert page.table.cell_text(0, 7) == "Not listed"


def test_history_shows_an_empty_state_without_sessions(window: MainWindow) -> None:
    window.show_page(PageKey.SESSIONS)
    page = cast(SessionsPage, window.page(PageKey.SESSIONS))
    assert page._stack.currentIndex() == 1 and not page.delete_button.isEnabled()


def test_double_click_opens_the_session_details(
    window: MainWindow, seeded: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[SessionDetailDialog] = []
    monkeypatch.setattr(SessionDetailDialog, "exec", lambda self: opened.append(self) or 0)
    window.show_page(PageKey.SESSIONS)
    page = cast(SessionsPage, window.page(PageKey.SESSIONS))
    page.search_box.setText("silver")
    page.refresh()
    page.table.object_activated.emit(page.table.object_at_position(0))
    assert len(opened) == 1 and opened[0].record.scenario_name == "Second scenario"


def test_session_details_show_every_section(
    qtbot: QtBot, seeded: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = cast(QApplication, QApplication.instance())
    record = next(s for s in seeded.sessions.search() if s.status is SessionStatus.FAILED)
    dialog = SessionDetailDialog(
        seeded.sessions.get_details(record.id), ThemeManager(app, ThemeMode.LIGHT)
    )
    qtbot.addWidget(dialog)

    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "Overview",
        "Timeline (2)",
        "Scenario settings",
        "Errors",
        "Screenshots (1)",
    ]
    assert dialog.timeline.row_count() == 2
    assert [dialog.timeline.cell_text(r, 1) for r in range(2)] == ["SESSION_STARTED", "ERROR"]
    settings = {
        dialog.settings_table.cell_text(r, 0): dialog.settings_table.cell_text(r, 1)
        for r in range(dialog.settings_table.row_count())
    }
    assert settings["browser.viewport_width"] == "1280" and settings["seed"] == "7"
    assert (
        dialog.error_table.row_count() == 1
        and dialog.error_table.cell_text(0, 2) == "page did not load"
    )
    assert dialog.shot_list.count() == 1
    item = dialog.shot_list.item(0)
    assert "Error" in item.text() and not item.icon().isNull()

    viewed: list[str] = []
    monkeypatch.setattr(
        session_dialog.ImageViewer, "exec", lambda self: viewed.append(self.windowTitle()) or 0
    )
    dialog.shot_list.itemDoubleClicked.emit(item)
    assert viewed == ["session_2_keyword_silver_error.png"]


def test_session_details_handle_missing_files_and_clean_sessions(
    qtbot: QtBot, seeded: AppContainer
) -> None:
    app = cast(QApplication, QApplication.instance())
    with seeded.database.session_scope() as session:
        repo = SessionRepository(session)
        clean = repo.add(make_session(NOW))
        repo.add_screenshot(
            Screenshot(
                session_id=clean.id,
                kind=ScreenshotKind.SESSION_END,
                file_path="C:/gone/missing.png",
            )
        )
    dialog = SessionDetailDialog(
        seeded.sessions.get_details(clean.id), ThemeManager(app, ThemeMode.DARK)
    )
    qtbot.addWidget(dialog)
    assert "(file missing)" in dialog.shot_list.item(0).text()
    assert not hasattr(dialog, "error_table")  # no errors: a plain note instead


def test_flatten_nested_settings() -> None:
    pairs = flatten({"a": {"b": 1, "c": None}, "list": [1, "x"], "name": "n"})
    assert [(p.key, p.value) for p in pairs] == [
        ("a.b", "1"),
        ("a.c", "—"),
        ("list", '[1, "x"]'),
        ("name", "n"),
    ]


def test_deleting_sessions_from_the_history(
    window: MainWindow, seeded: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter([False, True])
    monkeypatch.setattr(ConfirmDialog, "ask", classmethod(lambda *_a, **_k: next(answers)))
    window.show_page(PageKey.SESSIONS)
    page = cast(SessionsPage, window.page(PageKey.SESSIONS))
    page.table.selectRow(0)
    page.delete_button.click()
    assert page.table.row_count() == 4  # declined
    page.delete_button.click()
    assert page.table.row_count() == 3 and "Deleted 1 session" in window.notifier.toast.message()


def test_history_refreshes_when_a_run_finishes(window: MainWindow, seeded: AppContainer) -> None:
    window.show_page(PageKey.SESSIONS)
    page = cast(SessionsPage, window.page(PageKey.SESSIONS))
    with seeded.database.session_scope() as session:
        SessionRepository(session).add(make_session(NOW + timedelta(hours=1)))
    assert page.table.row_count() == 4
    window.runs.finished.emit(RunSummary())
    assert page.table.row_count() == 5


# -- logs page ---------------------------------------------------------------------------- #
def test_logs_page_streams_filters_and_clears(window: MainWindow, qtbot: QtBot) -> None:
    window.show_page(PageKey.LOGS)
    page = cast(LogsPage, window.page(PageKey.LOGS))
    log = get_logger("tests.logs_page")

    worker = threading.Thread(
        target=lambda: (
            log.info("hello_world", item=1),
            log.warning("careful_now"),
            log.error("broken_thing"),
        )
    )
    worker.start()
    worker.join()
    qtbot.waitUntil(lambda: any("broken_thing" in line for line in page.viewer.plain_lines()))
    text = "\n".join(page.viewer.plain_lines())
    assert "hello_world" in text and "WARNING" in text and "tests.logs_page" in text

    page.level_filter.setCurrentIndex(3)  # errors only
    assert [line for line in page.viewer.plain_lines() if "tests.logs_page" in line] == [
        line for line in page.viewer.plain_lines() if "broken_thing" in line
    ]
    page.level_filter.setCurrentIndex(0)
    page.search_box.setText("careful")
    page._render()
    assert page.viewer.line_count() == 1 and "careful_now" in page.viewer.plain_lines()[0]

    page.clear_view()
    assert page.viewer.line_count() == 0 and window._container.log_buffer.entries() == []
    assert "log file" in page.footer.text()


# -- reports page --------------------------------------------------------------------------- #
def test_reports_page_generates_and_exports(
    window: MainWindow, seeded: AppContainer, qtbot: QtBot, tmp_path: Path
) -> None:
    window.show_page(PageKey.REPORTS)
    page = cast(ReportsPage, window.page(PageKey.REPORTS))
    assert not any(b.isEnabled() for b in page.export_buttons.values())
    assert page.keyword_filter.findData("silver chain") > 0 and page.target_filter.count() == 3

    page.generate()
    assert not page.generate_button.isEnabled()  # busy while the worker runs
    qtbot.waitUntil(lambda: page.report is not None)
    assert page.generate_button.isEnabled()
    assert page.cards["Sessions"].value_text() == "4" and page.cards["Failed"].value_text() == "1"
    assert page.cards["Success rate"].value_text() == "33.3 %"
    assert page.table.row_count() == 4 and all(b.isEnabled() for b in page.export_buttons.values())

    exports = {"csv": "r.csv", "excel": "r.xlsx", "json": "r.json"}
    for kind, name in exports.items():
        page.export_to(kind, tmp_path / name)
        qtbot.waitUntil(lambda n=name: (tmp_path / n).exists() and page.generate_button.isEnabled())
    assert "Report exported" in window.notifier.toast.message()
    assert len(load_workbook(tmp_path / "r.xlsx")["Sessions"]["A"]) == 5
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["metrics"]["failed"] == 1


def test_reports_page_filters_narrow_the_report(
    window: MainWindow, seeded: AppContainer, qtbot: QtBot
) -> None:
    window.show_page(PageKey.REPORTS)
    page = cast(ReportsPage, window.page(PageKey.REPORTS))
    page.status_filter.setCurrentIndex(page.status_filter.findData(SessionStatus.FAILED))
    assert page.current_filter().status is SessionStatus.FAILED
    page.generate()
    qtbot.waitUntil(lambda: page.report is not None)
    assert page.table.row_count() == 1 and page.cards["Failed"].value_text() == "1"
    rows = preview_rows(page.report)  # type: ignore[arg-type]
    assert rows[0].keyword == "silver chain" and rows[0].position == "—"


def test_reports_page_rejects_an_inverted_date_range(window: MainWindow, qtbot: QtBot) -> None:
    window.show_page(PageKey.REPORTS)
    page = cast(ReportsPage, window.page(PageKey.REPORTS))
    page.date_from.enabled_box.setChecked(True)
    page.date_to.enabled_box.setChecked(True)
    page.date_from.edit.setDate(page.date_to.edit.date().addDays(3))
    page.generate()
    assert page.report is None and window.notifier.toast.tone() == "danger"
    assert page.generate_button.isEnabled()


def test_reports_page_reports_export_failures(
    window: MainWindow, seeded: AppContainer, qtbot: QtBot, tmp_path: Path
) -> None:
    window.show_page(PageKey.REPORTS)
    page = cast(ReportsPage, window.page(PageKey.REPORTS))
    page.generate()
    qtbot.waitUntil(lambda: page.report is not None)
    page.export_to("csv", tmp_path / "no_such_folder" / "x.csv")
    qtbot.waitUntil(
        lambda: window.notifier.toast.tone() == "danger" and page.generate_button.isEnabled()
    )
    assert "does not exist" in window.notifier.toast.message()
    assert all(b.isEnabled() for b in page.export_buttons.values())  # can retry
