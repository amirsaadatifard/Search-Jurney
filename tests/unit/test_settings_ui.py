"""Settings page, backup/restore from the GUI, configuration transfer and the demo button."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.app.paths import AppPaths
from src.domain.exceptions import AutomationError
from src.services.first_run_service import DEMO_SCENARIO_NAME
from src.services.settings_service import SettingKey
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.pages.dashboard_page import DashboardPage
from src.ui.pages.settings_page import SettingsPage
from src.ui.run_controller import RunPhase
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.confirm_dialog import ConfirmDialog
from tests.unit.test_run_ui import FakeRuns


@pytest.fixture
def fake_runs(container: AppContainer) -> FakeRuns:
    runs = FakeRuns()
    container.runs = runs  # type: ignore[assignment]
    return runs


@pytest.fixture
def window(container: AppContainer, fake_runs: FakeRuns) -> Iterator[MainWindow]:
    app = cast(QApplication, QApplication.instance())
    main_window = MainWindow(container, ThemeManager(app, ThemeMode.LIGHT))
    main_window.show()
    yield main_window
    main_window.runs.phase = RunPhase.IDLE
    main_window.close()
    main_window.deleteLater()


@pytest.fixture
def page(window: MainWindow) -> SettingsPage:
    window.show_page(PageKey.SETTINGS)
    return cast(SettingsPage, window.page(PageKey.SETTINGS))


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the real .env of the project."""
    path = tmp_path / ".env"
    monkeypatch.setattr(AppPaths, "env_file", property(lambda self: path))
    return path


def accept(monkeypatch: pytest.MonkeyPatch, answer: bool = True) -> list[str]:
    asked: list[str] = []

    def ask(_cls: Any, _parent: Any, title: str, *_a: Any, **_k: Any) -> bool:
        asked.append(title)
        return answer

    monkeypatch.setattr(ConfirmDialog, "ask", classmethod(ask))
    return asked


# -- appearance --------------------------------------------------------------------------- #
def test_theme_choice_is_applied_saved_and_synchronised(
    window: MainWindow, page: SettingsPage, container: AppContainer
) -> None:
    page.theme_combo.setCurrentIndex(page.theme_combo.findData(ThemeMode.DARK))
    assert window._theme.mode is ThemeMode.DARK
    assert container.settings.get(SettingKey.UI_THEME) == "dark"
    window.apply_theme_mode(ThemeMode.SYSTEM)  # e.g. from the sidebar buttons
    assert (
        page.theme_combo.currentData() is ThemeMode.SYSTEM
        or page.theme_combo.currentData() == "system"
    )
    assert window._sidebar._theme_buttons[ThemeMode.SYSTEM][0].isChecked()


# -- defaults ------------------------------------------------------------------------------- #
def test_defaults_are_loaded_saved_and_validated(
    page: SettingsPage, container: AppContainer, window: MainWindow
) -> None:
    assert (page.min_dwell.value(), page.max_dwell.value(), page.timeout.value()) == (5, 15, 30)
    assert not page.concurrent.isEnabled() and page.concurrent.value() == 1
    assert all(box.isChecked() for box in page.shot_boxes.values())

    page.min_dwell.setValue(40)
    page.max_dwell.setValue(10)
    page.save_defaults()
    assert page.default_errors["max_dwell_seconds"].isVisibleTo(page)
    assert "not be less" in page.default_errors["max_dwell_seconds"].text()
    assert container.settings.automation_defaults().min_dwell_seconds == 5  # nothing saved

    page.min_dwell.setValue(2)
    page.max_dwell.setValue(8)
    page.timeout.setValue(60)
    page.shot_boxes["error"].setChecked(False)
    page.save_defaults()
    saved = container.settings.automation_defaults()
    assert (saved.min_dwell_seconds, saved.max_dwell_seconds, saved.timeout_seconds) == (2, 8, 60)
    assert saved.screenshots["error"] is False and window.notifier.toast.tone() == "success"
    assert not page.default_errors["max_dwell_seconds"].isVisibleTo(page)


# -- locations and ports ------------------------------------------------------------------------ #
def test_port_changes_are_written_to_the_env_file(
    page: SettingsPage, env_file: Path, window: MainWindow
) -> None:
    assert page.location_changes() == {}
    page.target_port.setValue(9102)
    page.save_locations()
    assert env_file.read_text(encoding="utf-8").splitlines() == ["SJAT_TARGET_PORT=9102"]
    assert "Restart" in window.notifier.toast.message()


@pytest.mark.parametrize(("target", "fragment"), [(0, "target port"), (70000, "target port")])
def test_invalid_ports_are_rejected(
    page: SettingsPage, env_file: Path, window: MainWindow, target: int, fragment: str
) -> None:
    page.target_port.setValue(target)
    page.save_locations()
    assert not env_file.exists() and window.notifier.toast.tone() == "danger"
    assert fragment in window.notifier.toast.message()


def test_unusable_locations_are_rejected(
    page: SettingsPage, env_file: Path, window: MainWindow, tmp_path: Path
) -> None:
    folder = tmp_path / "a_folder"
    folder.mkdir()
    page.location_inputs["database_file"].setText(str(folder))
    page.save_locations()
    assert not env_file.exists() and "folder" in window.notifier.toast.message()

    page.location_inputs["database_file"].setText(str(tmp_path / "db" / "x.db"))
    page.location_inputs["logs_dir"].setText(str(tmp_path / "logs2"))
    page.save_locations()
    text = env_file.read_text(encoding="utf-8")
    assert f"SJAT_DATABASE_FILE={tmp_path / 'db' / 'x.db'}" in text and "SJAT_LOGS_DIR=" in text


def test_restoring_defaults_removes_the_keys(
    page: SettingsPage, env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file.write_text(
        "# mine\nSJAT_TARGET_PORT=9101\nSJAT_LOG_LEVEL=DEBUG\nOTHER=1\n", encoding="utf-8"
    )
    accept(monkeypatch, False)
    page._reset_locations()
    assert "SJAT_TARGET_PORT" in env_file.read_text(encoding="utf-8")
    accept(monkeypatch, True)
    page._reset_locations()
    assert env_file.read_text(encoding="utf-8") == "# mine\nSJAT_LOG_LEVEL=DEBUG\nOTHER=1\n"


# -- backups ------------------------------------------------------------------------------ #
def test_backup_restore_and_delete_from_the_gui(
    window: MainWindow,
    page: SettingsPage,
    container: AppContainer,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert page.backup_table.row_count() == 0 and not page.restore_button.isEnabled()
    page.backup_button.click()
    qtbot.waitUntil(lambda: page.backup_table.row_count() == 1)
    assert page.backup_button.isEnabled() and "Backup saved" in window.notifier.toast.message()

    container.keywords.create({"text": "added after the backup"})
    page.backup_table.selectRow(0)
    assert page.restore_button.isEnabled()

    asked = accept(monkeypatch, False)
    page.restore_button.click()
    assert (
        asked == ["Restore this backup?"] and container.keywords.search(text="added after") != []
    )  # declined

    accept(monkeypatch, True)
    page.restore_button.click()
    assert container.keywords.search(text="added after") == []
    assert (
        "Backup restored" in window.notifier.toast.message()
        and window.current_page_key() is PageKey.DASHBOARD
    )
    assert any("pre-restore" in b.name for b in container.backups.list_backups())

    window.show_page(PageKey.SETTINGS)
    page.backup_table.selectRow(0)
    page.delete_backup_button.click()
    assert page.backup_table.row_count() == len(container.backups.list_backups())


def test_a_bad_backup_is_reported_and_changes_nothing(
    page: SettingsPage,
    window: MainWindow,
    container: AppContainer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    junk = tmp_path / "junk.db"
    junk.write_text("not a database")
    accept(monkeypatch, True)
    assert page.restore_from(junk) is False
    assert (
        window.notifier.toast.tone() == "danger"
        and "not a database backup" in window.notifier.toast.message()
    )
    assert len(container.scenarios.search()) == 1


def test_restore_applies_the_theme_stored_in_the_backup(
    window: MainWindow, page: SettingsPage, container: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container.settings.set(SettingKey.UI_THEME, "dark")
    backup = container.backups.create_backup()
    container.settings.set(SettingKey.UI_THEME, "light")
    accept(monkeypatch, True)
    assert page.restore_from(backup.path)
    assert window._theme.mode is ThemeMode.DARK


# -- configuration transfer --------------------------------------------------------------- #
def test_export_and_import_from_the_gui(
    page: SettingsPage, window: MainWindow, container: AppContainer, qtbot: QtBot, tmp_path: Path
) -> None:
    target = tmp_path / "config.json"
    page.export_to(target)
    qtbot.waitUntil(target.exists)
    qtbot.waitUntil(lambda: "exported" in window.notifier.toast.message())
    assert json.loads(target.read_text(encoding="utf-8"))["format"] == "search-journey-config"

    def settled() -> bool:
        return page._tasks.pending == 0

    page.import_from(target)
    qtbot.waitUntil(settled)
    assert (
        "skipped" in window.notifier.toast.message() and window.notifier.toast.tone() == "success"
    )

    page.overwrite_box.setChecked(True)
    page.import_from(target)
    qtbot.waitUntil(settled)
    assert (
        "0 created" in window.notifier.toast.message()
        and "skipped" not in window.notifier.toast.message()
    )

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    page.import_from(bad)
    qtbot.waitUntil(settled)
    assert window.notifier.toast.tone() == "danger"
    assert "not valid JSON" in window.notifier.toast.message()


# -- demo --------------------------------------------------------------------------------- #
def test_demo_button_starts_one_cycle_and_opens_the_live_view(
    window: MainWindow, fake_runs: FakeRuns, container: AppContainer
) -> None:
    dashboard = cast(DashboardPage, window.page(PageKey.DASHBOARD))
    assert dashboard.demo_button.isEnabled()
    dashboard.demo_button.click()
    demo_id = container.scenarios.search()[0].id
    assert fake_runs.started_with == [[demo_id]] and fake_runs.overrides == [1]
    assert window.current_page_key() is PageKey.LIVE_VIEW and window.runs.phase is RunPhase.RUNNING
    assert not dashboard.demo_button.isEnabled()  # cannot start a second run


def test_demo_repairs_data_before_starting(
    window: MainWindow, fake_runs: FakeRuns, container: AppContainer
) -> None:
    container.scenarios.delete(container.scenarios.search()[0].id)
    window.start_demo()
    assert (
        container.scenarios.search()[0].name == DEMO_SCENARIO_NAME
        and len(fake_runs.started_with) == 1
    )


def test_demo_errors_are_shown_not_raised(window: MainWindow, fake_runs: FakeRuns) -> None:
    fake_runs.fail_start = AutomationError("Search Simulator could not start.")
    window.start_demo()
    assert (
        window.notifier.toast.tone() == "danger"
        and window.current_page_key() is not PageKey.LIVE_VIEW
    )
