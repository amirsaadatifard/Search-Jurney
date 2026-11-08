"""Typed access to persisted application settings."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

from src.app.constants import (
    DEFAULT_MAX_DWELL_SECONDS,
    DEFAULT_MIN_DWELL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CONCURRENT_SESSIONS,
)
from src.database.repositories.settings_repository import SettingsRepository
from src.database.session import Database
from src.domain.exceptions import InvalidConfigurationError
from src.logging_config import get_logger

log = get_logger(__name__)

MAX_DWELL: Final = 3600
MIN_TIMEOUT: Final = 5
MAX_TIMEOUT: Final = 300


class SettingKey:
    """Names of the known settings (avoids scattering string literals)."""

    UI_THEME: Final = "ui.theme"
    FIRST_RUN_COMPLETED: Final = "app.first_run_completed"
    DEFAULT_MIN_DWELL: Final = "automation.default_min_dwell_seconds"
    DEFAULT_MAX_DWELL: Final = "automation.default_max_dwell_seconds"
    DEFAULT_TIMEOUT: Final = "automation.default_timeout_seconds"
    MAX_CONCURRENT_SESSIONS: Final = "automation.max_concurrent_sessions"
    SCREENSHOT_POLICY: Final = "automation.screenshot_policy"


@dataclass(frozen=True, slots=True)
class AutomationDefaults:
    """User-editable defaults for new scenarios and for every run."""

    min_dwell_seconds: int = DEFAULT_MIN_DWELL_SECONDS
    max_dwell_seconds: int = DEFAULT_MAX_DWELL_SECONDS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    screenshots: dict[str, bool] = field(
        default_factory=lambda: {
            "search_results": True,
            "target_opened": True,
            "session_end": True,
            "error": True,
        }
    )
    max_concurrent_sessions: int = MAX_CONCURRENT_SESSIONS  # fixed at 1; kept for future pools

    def validate(self) -> None:
        """Raise :class:`InvalidConfigurationError` with a message per bad field."""
        errors: dict[str, str] = {}
        if not 1 <= self.min_dwell_seconds <= MAX_DWELL:
            errors["min_dwell_seconds"] = f"Use 1 to {MAX_DWELL} seconds."
        if not 1 <= self.max_dwell_seconds <= MAX_DWELL:
            errors["max_dwell_seconds"] = f"Use 1 to {MAX_DWELL} seconds."
        elif self.max_dwell_seconds < self.min_dwell_seconds:
            errors["max_dwell_seconds"] = "The maximum must not be less than the minimum."
        if not MIN_TIMEOUT <= self.timeout_seconds <= MAX_TIMEOUT:
            errors["timeout_seconds"] = f"Use {MIN_TIMEOUT} to {MAX_TIMEOUT} seconds."
        if errors:
            raise InvalidConfigurationError("Some settings need attention.", field_errors=errors)


class SettingsService:
    """Stores settings as JSON so booleans, numbers and objects round-trip exactly."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, key: str, default: Any = None) -> Any:
        """Return the decoded value for ``key`` or ``default`` if unset/corrupt."""
        with self._database.session_scope() as session:
            raw = SettingsRepository(session).get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("setting_decode_failed", key=key)
            return default

    def set(self, key: str, value: Any) -> None:
        """Persist ``value`` (must be JSON serialisable)."""
        encoded = json.dumps(value)
        with self._database.session_scope() as session:
            SettingsRepository(session).set(key, encoded)

    def automation_defaults(self) -> AutomationDefaults:
        """The stored defaults; missing or damaged values fall back to the built-in ones."""
        base = AutomationDefaults()

        def number(key: str, fallback: int) -> int:
            value = self.get(key, fallback)
            return value if isinstance(value, int) and not isinstance(value, bool) else fallback

        policy = self.get(SettingKey.SCREENSHOT_POLICY, {})
        screenshots = dict(base.screenshots)
        if isinstance(policy, dict):
            screenshots.update({k: bool(v) for k, v in policy.items() if k in screenshots})
        return AutomationDefaults(
            min_dwell_seconds=number(SettingKey.DEFAULT_MIN_DWELL, base.min_dwell_seconds),
            max_dwell_seconds=number(SettingKey.DEFAULT_MAX_DWELL, base.max_dwell_seconds),
            timeout_seconds=number(SettingKey.DEFAULT_TIMEOUT, base.timeout_seconds),
            screenshots=screenshots,
        )

    def save_automation_defaults(self, defaults: AutomationDefaults) -> None:
        defaults.validate()
        self.set(SettingKey.DEFAULT_MIN_DWELL, defaults.min_dwell_seconds)
        self.set(SettingKey.DEFAULT_MAX_DWELL, defaults.max_dwell_seconds)
        self.set(SettingKey.DEFAULT_TIMEOUT, defaults.timeout_seconds)
        self.set(SettingKey.SCREENSHOT_POLICY, dict(defaults.screenshots))

    def all(self) -> dict[str, Any]:
        """Return every stored setting, decoded. Corrupt entries are skipped."""
        with self._database.session_scope() as session:
            raw_items = SettingsRepository(session).all()
        decoded: dict[str, Any] = {}
        for key, raw in raw_items.items():
            try:
                decoded[key] = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("setting_decode_failed", key=key)
        return decoded
