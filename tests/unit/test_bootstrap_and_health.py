"""Bootstrap wiring, settings persistence, logging and health checks."""

from __future__ import annotations

import json
import socket

from src.app.config import AppConfig
from src.app.constants import LOOPBACK_HOST
from src.app.container import AppContainer
from src.app.health import HealthStatus, StartupHealthService, is_port_available
from src.app.paths import AppPaths
from src.database.session import Database
from src.logging_config import get_logger
from src.services.settings_service import SettingKey, SettingsService


def test_bootstrap_creates_storage_and_database(container: AppContainer) -> None:
    assert container.paths.database_file.is_file()
    assert container.database.ping()
    assert all(directory.is_dir() for directory in container.paths.directories)


def test_health_report_covers_core_checks(container: AppContainer) -> None:
    names = {result.name for result in container.health_report.results}
    assert {"Python runtime", "Data directories", "Database", "Playwright browser"} <= names
    by_name = {result.name: result for result in container.health_report.results}
    assert by_name["Database"].status is HealthStatus.OK
    assert by_name["Data directories"].status is HealthStatus.OK
    assert container.health_report.worst is not HealthStatus.ERROR


def test_occupied_port_is_reported_as_warning(app_paths: AppPaths, database: Database) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind((LOOPBACK_HOST, 0))
        port = blocker.getsockname()[1]
        assert not is_port_available(port)
        config = AppConfig(data_dir=app_paths.data_dir, search_port=port, target_port=18999)
        report = StartupHealthService(config, app_paths, database).run()
    by_name = {result.name: result for result in report.results}
    assert by_name["Search simulator port"].status is HealthStatus.WARNING
    assert report.worst is HealthStatus.WARNING
    assert report.passed == len(report.results) - 1


def test_missing_directory_is_reported_as_error(app_paths: AppPaths, database: Database) -> None:
    app_paths.backups_dir.rmdir()
    config = AppConfig(data_dir=app_paths.data_dir, search_port=18765, target_port=18766)
    report = StartupHealthService(config, app_paths, database).run()
    assert report.worst is HealthStatus.ERROR


def test_settings_round_trip_preserves_types(database: Database) -> None:
    settings = SettingsService(database)
    assert settings.get(SettingKey.UI_THEME, "system") == "system"
    settings.set(SettingKey.UI_THEME, "dark")
    settings.set(SettingKey.DEFAULT_MIN_DWELL, 7)
    settings.set(SettingKey.SCREENSHOT_POLICY, {"on_error": True})
    settings.set(SettingKey.UI_THEME, "light")  # overwrite
    assert settings.get(SettingKey.UI_THEME) == "light"
    assert settings.get(SettingKey.DEFAULT_MIN_DWELL) == 7
    assert settings.all()[SettingKey.SCREENSHOT_POLICY] == {"on_error": True}


def test_logs_reach_file_and_ui_buffer(container: AppContainer) -> None:
    seen: list[str] = []
    container.log_buffer.subscribe(lambda entry: seen.append(entry.message))
    get_logger("tests.logging").info("sample_event", session_id=42)

    assert any("sample_event" in message and "session_id=42" in message for message in seen)
    lines = container.paths.log_file.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["event"] == "sample_event"
    assert record["session_id"] == 42
    assert record["level"] == "info"
