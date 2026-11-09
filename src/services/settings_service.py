"""Typed access to persisted application settings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

from src.app.constants import (
    DEFAULT_MAX_DWELL_SECONDS,
    DEFAULT_MAX_SEARCH_PAGES,
    DEFAULT_MIN_DWELL_SECONDS,
    DEFAULT_SEARCH_RESULTS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CONCURRENT_SESSIONS,
    MAX_SEARCH_RESULTS,
)
from src.database.repositories.settings_repository import SettingsRepository
from src.database.session import Database
from src.domain.exceptions import InvalidConfigurationError
from src.logging_config import get_logger

log = get_logger(__name__)

MAX_DWELL: Final = 3600
MIN_TIMEOUT: Final = 5
MAX_TIMEOUT: Final = 300
MIN_REQUESTED_RESULTS: Final = 10  # Google returns results in pages of ten
MIN_SEARCH_PAGES: Final = 1
MAX_SEARCH_PAGES: Final = 10
_LANGUAGE_PATTERN: Final = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{1,3})?$")
_COUNTRY_PATTERN: Final = re.compile(r"^[A-Z]{2}$")


class SettingKey:
    """Names of the known settings (avoids scattering string literals)."""

    UI_THEME: Final = "ui.theme"
    FIRST_RUN_COMPLETED: Final = "app.first_run_completed"
    DEFAULT_MIN_DWELL: Final = "automation.default_min_dwell_seconds"
    DEFAULT_MAX_DWELL: Final = "automation.default_max_dwell_seconds"
    DEFAULT_TIMEOUT: Final = "automation.default_timeout_seconds"
    MAX_CONCURRENT_SESSIONS: Final = "automation.max_concurrent_sessions"
    SCREENSHOT_POLICY: Final = "automation.screenshot_policy"
    SEARCH_LANGUAGE: Final = "search.language"
    SEARCH_COUNTRY: Final = "search.country"
    SEARCH_RESULTS: Final = "search.results_per_query"
    SEARCH_MAX_PAGES: Final = "search.max_result_pages"


@dataclass(frozen=True, slots=True)
class AutomationDefaults:
    """User-editable defaults for new scenarios and for every run."""

    min_dwell_seconds: int = DEFAULT_MIN_DWELL_SECONDS
    max_dwell_seconds: int = DEFAULT_MAX_DWELL_SECONDS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    screenshots: dict[str, bool] = field(
        default_factory=lambda: {
            "target_opened": True,
            "session_end": True,
            "error": True,
        }
    )
    max_concurrent_sessions: int = MAX_CONCURRENT_SESSIONS  # fixed at 1; kept for future pools
    # How every scenario searches Google in the browser for its target site.
    search_language: str = ""  # empty = the interface language Google picks for this IP
    search_country: str = ""  # empty = no country boost
    requested_results: int = DEFAULT_SEARCH_RESULTS
    max_search_pages: int = DEFAULT_MAX_SEARCH_PAGES

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
        if not MIN_REQUESTED_RESULTS <= self.requested_results <= MAX_SEARCH_RESULTS:
            errors["requested_results"] = (
                f"A results page holds between {MIN_REQUESTED_RESULTS} and "
                f"{MAX_SEARCH_RESULTS} links."
            )
        if not MIN_SEARCH_PAGES <= self.max_search_pages <= MAX_SEARCH_PAGES:
            errors["max_search_pages"] = (
                f"Walk between {MIN_SEARCH_PAGES} and {MAX_SEARCH_PAGES} result pages."
            )
        language = self.search_language.strip()
        if language and not _LANGUAGE_PATTERN.fullmatch(language):
            errors["search_language"] = "Use a code such as 'de' or 'en-US', or leave it empty."
        country = self.search_country.strip()
        if country and not _COUNTRY_PATTERN.fullmatch(country):
            errors["search_country"] = (
                "Use a two-letter country code such as 'DE', or leave it empty."
            )
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

        def text(key: str, fallback: str) -> str:
            value = self.get(key, fallback)
            return value.strip() if isinstance(value, str) else fallback

        return AutomationDefaults(
            min_dwell_seconds=number(SettingKey.DEFAULT_MIN_DWELL, base.min_dwell_seconds),
            max_dwell_seconds=number(SettingKey.DEFAULT_MAX_DWELL, base.max_dwell_seconds),
            timeout_seconds=number(SettingKey.DEFAULT_TIMEOUT, base.timeout_seconds),
            screenshots=screenshots,
            search_language=text(SettingKey.SEARCH_LANGUAGE, base.search_language).lower(),
            search_country=text(SettingKey.SEARCH_COUNTRY, base.search_country).upper(),
            requested_results=number(SettingKey.SEARCH_RESULTS, base.requested_results),
            max_search_pages=number(SettingKey.SEARCH_MAX_PAGES, base.max_search_pages),
        )

    def save_automation_defaults(self, defaults: AutomationDefaults) -> None:
        defaults.validate()
        self.set(SettingKey.DEFAULT_MIN_DWELL, defaults.min_dwell_seconds)
        self.set(SettingKey.DEFAULT_MAX_DWELL, defaults.max_dwell_seconds)
        self.set(SettingKey.DEFAULT_TIMEOUT, defaults.timeout_seconds)
        self.set(SettingKey.SCREENSHOT_POLICY, dict(defaults.screenshots))
        self.set(SettingKey.SEARCH_LANGUAGE, defaults.search_language.lower())
        self.set(SettingKey.SEARCH_COUNTRY, defaults.search_country.upper())
        self.set(SettingKey.SEARCH_RESULTS, defaults.requested_results)
        self.set(SettingKey.SEARCH_MAX_PAGES, defaults.max_search_pages)

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
