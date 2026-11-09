"""Configuration parsing and path resolution."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.app.config import AppConfig
from src.app.constants import DEFAULT_TARGET_PORT
from src.app.paths import AppPaths


def test_defaults_when_environment_is_empty() -> None:
    config = AppConfig.from_environment({})
    assert config.target_port == DEFAULT_TARGET_PORT
    assert config.log_level == "INFO"


def test_environment_overrides_are_applied() -> None:
    config = AppConfig.from_environment(
        {"SJAT_TARGET_PORT": "9001", "SJAT_LOG_LEVEL": "debug", "UNRELATED": "x"}
    )
    assert config.target_port == 9001
    assert config.log_level == "DEBUG"


def test_environment_wins_over_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nSJAT_TARGET_PORT='9200'\nSJAT_DATA_DIR=elsewhere\n", encoding="utf-8"
    )
    config = AppConfig.from_environment({"SJAT_TARGET_PORT": "9300"}, env_file=env_file)
    assert config.target_port == 9300
    # A path is a path by the time it reaches the application, whichever source it
    # came from - which is exactly why the value above is not quoted like a URL.
    assert config.data_dir == Path("elsewhere")


@pytest.mark.parametrize("port", ["80", "70000", "abc"])
def test_invalid_port_is_rejected(port: str) -> None:
    with pytest.raises(ValidationError):
        AppConfig.from_environment({"SJAT_TARGET_PORT": port})


def test_relative_data_dir_is_anchored_at_root(tmp_path: Path) -> None:
    paths = AppPaths.from_data_dir(Path("data"), root=tmp_path)
    assert paths.data_dir == tmp_path / "data"
    assert paths.data_dir.is_absolute()
    assert paths.database_file.parent == paths.data_dir
    assert paths.log_file.parent == paths.logs_dir


def test_ensure_directories_is_idempotent(tmp_path: Path) -> None:
    paths = AppPaths.from_data_dir(tmp_path / "storage", root=tmp_path)
    paths.ensure_directories()
    paths.ensure_directories()
    assert all(directory.is_dir() for directory in paths.directories)


def test_screenshot_folder_is_dated(app_paths: AppPaths) -> None:
    folder = app_paths.screenshots_dir_for(date(2026, 1, 31))
    assert folder == app_paths.screenshots_dir / "2026-01-31"
