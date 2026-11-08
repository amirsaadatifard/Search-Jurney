"""Startup health checks.

Each check is independent and never raises: a failing check is reported, not
fatal, so the GUI can still open and explain what is wrong.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from src.app.config import AppConfig
from src.app.constants import LOOPBACK_HOST
from src.app.paths import AppPaths
from src.database.session import Database
from src.logging_config import get_logger

log = get_logger(__name__)

MINIMUM_PYTHON: tuple[int, int] = (3, 12)


class PortOwner(Protocol):
    """Something that can tell whether the application itself occupies a port."""

    def is_serving_port(self, port: int) -> bool: ...


class HealthStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Outcome of one check, ready to be shown to the user."""

    name: str
    status: HealthStatus
    detail: str


@dataclass(frozen=True, slots=True)
class HealthReport:
    """All check results plus convenience aggregates."""

    results: tuple[HealthCheckResult, ...]

    @property
    def worst(self) -> HealthStatus:
        statuses = {result.status for result in self.results}
        if HealthStatus.ERROR in statuses:
            return HealthStatus.ERROR
        if HealthStatus.WARNING in statuses:
            return HealthStatus.WARNING
        return HealthStatus.OK

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status is HealthStatus.OK)

    def summary(self) -> str:
        return f"{self.passed}/{len(self.results)} checks passed"


class StartupHealthService:
    """Verifies that the environment can run the application."""

    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        database: Database,
        servers: PortOwner | None = None,
    ) -> None:
        self._config = config
        self._paths = paths
        self._database = database
        self._servers = servers

    def run(self) -> HealthReport:
        """Execute every check and log the outcome."""
        checks: tuple[Callable[[], HealthCheckResult], ...] = (
            self._check_python,
            self._check_directories,
            self._check_database,
            self._check_playwright,
            self._check_web_engine,
            lambda: self._check_port("Search simulator port", self._config.search_port),
            lambda: self._check_port("Test site port", self._config.target_port),
        )
        results = tuple(self._safely(check) for check in checks)
        report = HealthReport(results)
        for result in results:
            log.info("health_check", check=result.name, status=result.status.value)
        return report

    @staticmethod
    def _safely(check: Callable[[], HealthCheckResult]) -> HealthCheckResult:
        try:
            return check()
        except Exception as exc:  # a check must never break startup
            log.exception("health_check_crashed")
            return HealthCheckResult("Internal check", HealthStatus.ERROR, str(exc))

    @staticmethod
    def _check_python() -> HealthCheckResult:
        version = ".".join(str(part) for part in sys.version_info[:3])
        if sys.version_info[:2] >= MINIMUM_PYTHON:
            return HealthCheckResult("Python runtime", HealthStatus.OK, f"Python {version}")
        return HealthCheckResult(
            "Python runtime", HealthStatus.ERROR, f"Python {version} found, 3.12+ required"
        )

    def _check_directories(self) -> HealthCheckResult:
        name = "Data directories"
        for directory in self._paths.directories:
            if not directory.is_dir():
                return HealthCheckResult(name, HealthStatus.ERROR, f"Missing: {directory}")
            try:
                with tempfile.TemporaryFile(dir=directory):
                    pass
            except OSError as exc:
                return HealthCheckResult(name, HealthStatus.ERROR, f"Not writable: {exc}")
        return HealthCheckResult(name, HealthStatus.OK, str(self._paths.data_dir))

    def _check_database(self) -> HealthCheckResult:
        name = "Database"
        try:
            self._database.ping()
        except Exception as exc:
            return HealthCheckResult(name, HealthStatus.ERROR, f"Connection failed: {exc}")
        return HealthCheckResult(name, HealthStatus.OK, "SQLite connection established")

    @staticmethod
    def _check_playwright() -> HealthCheckResult:
        name = "Playwright browser"
        if importlib.util.find_spec("playwright") is None:
            return HealthCheckResult(
                name, HealthStatus.ERROR, "Package missing. Run: pip install playwright"
            )
        if find_playwright_chromium() is None:
            return HealthCheckResult(
                name,
                HealthStatus.WARNING,
                "Chromium not installed. Run: python -m playwright install chromium",
            )
        return HealthCheckResult(name, HealthStatus.OK, "Chromium is installed")

    @staticmethod
    def _check_web_engine() -> HealthCheckResult:
        name = "Qt WebEngine"
        if importlib.util.find_spec("PySide6.QtWebEngineWidgets") is None:
            return HealthCheckResult(
                name, HealthStatus.WARNING, "Not available; the embedded live view is disabled"
            )
        return HealthCheckResult(name, HealthStatus.OK, "Embedded browser view available")

    def _check_port(self, name: str, port: int) -> HealthCheckResult:
        if self._servers is not None and self._servers.is_serving_port(port):
            return HealthCheckResult(
                name, HealthStatus.OK, f"{LOOPBACK_HOST}:{port} is served by this application"
            )
        if is_port_available(port):
            return HealthCheckResult(name, HealthStatus.OK, f"{LOOPBACK_HOST}:{port} is free")
        return HealthCheckResult(
            name,
            HealthStatus.WARNING,
            f"{LOOPBACK_HOST}:{port} is already in use; change it in Settings",
        )


def is_port_available(port: int, host: str = LOOPBACK_HOST) -> bool:
    """Return ``True`` if ``host:port`` can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def playwright_browsers_dir() -> Path:
    """Return the directory where Playwright stores downloaded browsers."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override != "0":
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def find_playwright_chromium() -> Path | None:
    """Return the installed Chromium folder, or ``None`` if it was never downloaded."""
    browsers_dir = playwright_browsers_dir()
    if not browsers_dir.is_dir():
        return None
    candidates = sorted(
        entry
        for entry in browsers_dir.iterdir()
        if entry.is_dir() and entry.name.startswith("chromium") and any(entry.iterdir())
    )
    return candidates[-1] if candidates else None
