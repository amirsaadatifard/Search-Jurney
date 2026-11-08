"""Export and import of the application configuration as JSON.

What is exported: settings that belong to the user (theme, automation defaults),
browser profiles, proxy profiles, target sites, keywords and scenarios.

What is **never** exported: passwords (a proxy only records *that* a password
exists), session history, screenshots, logs.

On import everything is validated first (a file with any invalid entry is
rejected as a whole); then items are created. Items that already exist (same
name) are kept unless ``overwrite`` is set. Target sites always arrive *not
approved*: approving a site for automation is a decision the user takes on this
machine, never something a file can grant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from src.app.constants import APP_VERSION
from src.domain.exceptions import AppError, InvalidConfigurationError
from src.domain.schemas import (
    BrowserProfileInput,
    KeywordInput,
    ProxyProfileInput,
    ScenarioInput,
    TargetSiteInput,
    to_field_errors,
)
from src.logging_config import get_logger
from src.reports.common import validate_output_path
from src.services.browser_profile_service import BrowserProfileService
from src.services.keyword_service import KeywordService
from src.services.proxy_service import ProxyService
from src.services.scenario_service import ScenarioService
from src.services.settings_service import SettingKey, SettingsService
from src.services.target_site_service import TargetSiteService

log = get_logger(__name__)

FORMAT: Final = "search-journey-config"
VERSION: Final = 1
MAX_BYTES: Final = 5 * 1024 * 1024
MAX_ITEMS: Final = 5000
SECTIONS: Final = (
    "browser_profiles",
    "proxy_profiles",
    "target_sites",
    "keywords",
    "scenarios",
)
THEMES: Final = frozenset({"light", "dark", "system"})
# ``search_results`` is accepted so a configuration exported before the search API
# replaced the local simulator still imports; nothing writes it any more (see
# ``AutomationDefaults``).
POLICY_KEYS: Final = ("search_results", "target_opened", "session_end", "error")
_REFERENCE_FIELDS: Final = {
    "target_site": "target_site_id",
    "browser_profile": "browser_profile_id",
    "proxy_profile": "proxy_profile_id",
}


@dataclass(slots=True)
class ImportResult:
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def count(self, bucket: dict[str, int], section: str) -> None:
        bucket[section] = bucket.get(section, 0) + 1

    @property
    def total_created(self) -> int:
        return sum(self.created.values())

    @property
    def total_updated(self) -> int:
        return sum(self.updated.values())

    def summary(self) -> str:
        parts = [f"{self.total_created} created", f"{self.total_updated} updated"]
        if self.skipped:
            parts.append(f"{sum(self.skipped.values())} skipped (already exist)")
        if self.errors:
            parts.append(f"{len(self.errors)} could not be imported")
        return ", ".join(parts)


class ConfigTransferService:
    def __init__(
        self,
        settings: SettingsService,
        keywords: KeywordService,
        target_sites: TargetSiteService,
        browser_profiles: BrowserProfileService,
        proxies: ProxyService,
        scenarios: ScenarioService,
    ) -> None:
        self._settings = settings
        self._keywords = keywords
        self._sites = target_sites
        self._browsers = browser_profiles
        self._proxies = proxies
        self._scenarios = scenarios

    # -- export --------------------------------------------------------------------- #
    def export_dict(self) -> dict[str, Any]:
        sites = self._sites.search()
        browsers = self._browsers.list()
        proxies = self._proxies.list()
        site_names = {s.id: s.display_name for s in sites}
        browser_names = {b.id: b.name for b in browsers}
        proxy_names = {p.id: p.name for p in proxies}

        def dump(schema: type[BaseModel], obj: object, **extra: Any) -> dict[str, Any]:
            values = {
                name: getattr(obj, name) for name in schema.model_fields if hasattr(obj, name)
            }
            return schema.model_validate(values | extra).model_dump(mode="json")

        scenarios = []
        for s in self._scenarios.search():
            entry = dump(
                ScenarioInput, s, target_site_id=1, browser_profile_id=1, proxy_profile_id=None
            )
            for key in _REFERENCE_FIELDS.values():
                entry.pop(key)
            entry["target_site"] = site_names[s.target_site_id]
            entry["browser_profile"] = browser_names[s.browser_profile_id]
            entry["proxy_profile"] = (
                proxy_names.get(s.proxy_profile_id) if s.proxy_profile_id else None
            )
            scenarios.append(entry)

        return {
            "format": FORMAT,
            "version": VERSION,
            "app_version": APP_VERSION,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": "Passwords are never exported. Approve target sites again after import.",
            "settings": {
                key: self._settings.get(key)
                for key in _SETTING_KEYS
                if self._settings.get(key) is not None
            },
            "browser_profiles": [dump(BrowserProfileInput, b) for b in browsers],
            "proxy_profiles": [
                dump(ProxyProfileInput, p) | {"password_stored": p.has_password} for p in proxies
            ],
            "target_sites": [dump(TargetSiteInput, s, base_url=s.base_url) for s in sites],
            "keywords": [dump(KeywordInput, k) for k in self._keywords.search()],
            "scenarios": scenarios,
        }

    def export_file(self, path: Path) -> Path:
        target = validate_output_path(path, ".json")
        target.write_text(
            json.dumps(self.export_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log.info("config_exported", file=target.name)
        return target

    # -- import --------------------------------------------------------------------- #
    def import_file(self, path: Path, overwrite: bool = False) -> ImportResult:
        if not path.is_file():
            raise InvalidConfigurationError("The file does not exist.")
        if path.stat().st_size > MAX_BYTES:
            raise InvalidConfigurationError("The file is larger than 5 MB.")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidConfigurationError("The file is not valid JSON.") from exc
        return self.import_dict(data, overwrite)

    def import_dict(self, data: object, overwrite: bool = False) -> ImportResult:
        """Validate ``data`` completely, then apply it."""
        document = self._check_document(data)
        validated = self._validate_all(document)

        result = ImportResult()
        self._import_browsers(validated["browser_profiles"], result, overwrite)
        self._import_proxies(validated["proxy_profiles"], result, overwrite)
        self._import_sites(validated["target_sites"], result, overwrite)
        self._import_keywords(validated["keywords"], result, overwrite)
        self._import_scenarios(validated["scenarios"], result, overwrite)
        self._import_settings(document.get("settings", {}), result)
        if any(item.get("password_stored") for item in document.get("proxy_profiles", [])):
            result.notes.append("Proxy passwords are not part of exports; enter them again.")
        if result.created.get("target_sites"):
            result.notes.append(
                "Imported target sites are not approved; approve them before running."
            )
        log.info("config_imported", summary=result.summary())
        return result

    # -- validation ------------------------------------------------------------------- #
    @staticmethod
    def _check_document(data: object) -> dict[str, Any]:
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise InvalidConfigurationError("This is not a Search Journey configuration file.")
        if data.get("version") != VERSION:
            raise InvalidConfigurationError(
                f"Unsupported configuration version ({data.get('version')!r}); expected {VERSION}."
            )
        for section in SECTIONS:
            items = data.get(section, [])
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                raise InvalidConfigurationError(f"'{section}' must be a list of objects.")
            if len(items) > MAX_ITEMS:
                raise InvalidConfigurationError(f"'{section}' has too many entries.")
        return data

    def _validate_all(self, document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        schemas: dict[str, type[BaseModel]] = {
            "browser_profiles": BrowserProfileInput,
            "proxy_profiles": ProxyProfileInput,
            "target_sites": TargetSiteInput,
            "keywords": KeywordInput,
        }
        problems: list[str] = []
        validated: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTIONS}
        for section, schema in schemas.items():
            for index, raw in enumerate(document.get(section, []), start=1):
                item = {k: v for k, v in raw.items() if k in schema.model_fields}
                try:
                    validated[section].append(schema.model_validate(item).model_dump())
                except ValidationError as exc:
                    problems.append(f"{section} #{index}: {_describe(to_field_errors(exc))}")
        for index, raw in enumerate(document.get("scenarios", []), start=1):
            item = {k: v for k, v in raw.items() if k in ScenarioInput.model_fields}
            item |= {"target_site_id": 1, "browser_profile_id": 1, "proxy_profile_id": None}
            try:
                scenario = ScenarioInput.model_validate(item).model_dump()
            except ValidationError as exc:
                problems.append(f"scenarios #{index}: {_describe(to_field_errors(exc))}")
                continue
            for key in ("target_site", "browser_profile"):
                if not isinstance(raw.get(key), str) or not raw[key].strip():
                    problems.append(f"scenarios #{index}: '{key}' is required.")
            scenario["_refs"] = {key: raw.get(key) for key in _REFERENCE_FIELDS}
            validated["scenarios"].append(scenario)
        settings = document.get("settings", {})
        if not isinstance(settings, dict):
            problems.append("settings: must be an object.")
        else:
            problems.extend(_settings_problems(settings))
        if problems:
            raise InvalidConfigurationError(
                "The file contains invalid entries and nothing was imported:\n- "
                + "\n- ".join(problems[:20]),
                field_errors={"file": "; ".join(problems[:20])},
            )
        return validated

    # -- applying --------------------------------------------------------------------- #
    def _import_browsers(
        self, items: list[dict[str, Any]], result: ImportResult, overwrite: bool
    ) -> None:
        existing = {p.name.lower(): p for p in self._browsers.list()}
        for item in items:
            self._apply(
                "browser_profiles", item["name"], item, existing,
                self._browsers.create, self._browsers.update, result, overwrite,
            )  # fmt: skip

    def _import_proxies(
        self, items: list[dict[str, Any]], result: ImportResult, overwrite: bool
    ) -> None:
        existing = {p.name.lower(): p for p in self._proxies.list()}
        for item in items:
            self._apply(
                "proxy_profiles", item["name"], item, existing,
                self._proxies.create, self._proxies.update, result, overwrite,
            )  # fmt: skip

    def _import_sites(
        self, items: list[dict[str, Any]], result: ImportResult, overwrite: bool
    ) -> None:
        existing = {s.display_name.lower(): s for s in self._sites.search()}
        for item in items:
            item = item | {"approved_for_automation": False}  # a file can never grant approval
            self._apply(
                "target_sites",
                item["display_name"],
                item,
                existing,
                self._sites.create,
                self._sites.update,
                result,
                overwrite,
            )

    def _import_keywords(
        self, items: list[dict[str, Any]], result: ImportResult, overwrite: bool
    ) -> None:
        existing = {k.text.lower(): k for k in self._keywords.search()}
        for item in items:
            self._apply(
                "keywords",
                item["text"],
                item,
                existing,
                self._keywords.create,
                self._keywords.update,
                result,
                overwrite,
            )

    def _import_scenarios(
        self, items: list[dict[str, Any]], result: ImportResult, overwrite: bool
    ) -> None:
        sites = {s.display_name.lower(): s for s in self._sites.search()}
        browsers = {b.name.lower(): b for b in self._browsers.list()}
        proxies = {p.name.lower(): p for p in self._proxies.list()}
        existing = {s.name.lower(): s for s in self._scenarios.search()}
        for item in items:
            refs = item.pop("_refs")
            site = sites.get(str(refs["target_site"]).lower())
            browser = browsers.get(str(refs["browser_profile"]).lower())
            proxy_name = refs["proxy_profile"]
            proxy = proxies.get(str(proxy_name).lower()) if proxy_name else None
            problem = (
                f"target site '{refs['target_site']}' does not exist"
                if site is None
                else (
                    f"browser profile '{refs['browser_profile']}' does not exist"
                    if browser is None
                    else (
                        f"proxy profile '{proxy_name}' does not exist"
                        if proxy_name and proxy is None
                        else None
                    )
                )
            )
            if problem or site is None or browser is None:
                result.errors.append(f"scenarios '{item['name']}': {problem}")
                continue
            # A scenario on a site that is not approved here may be stored, but never enabled.
            data = item | {
                "target_site_id": site.id,
                "browser_profile_id": browser.id,
                "proxy_profile_id": proxy.id if proxy else None,
                "enabled": bool(item["enabled"]) and site.is_automation_allowed,
            }

            def create(values: dict[str, Any]) -> object:
                return self._scenarios.create(values, require_approved=False)

            def update(scenario_id: int, values: dict[str, Any]) -> object:
                return self._scenarios.update(scenario_id, values, require_approved=False)

            self._apply(
                "scenarios", item["name"], data, existing, create, update, result, overwrite
            )

    def _import_settings(self, settings: dict[str, Any], result: ImportResult) -> None:
        for key in _SETTING_KEYS:
            if key in settings:
                self._settings.set(key, settings[key])
                result.count(result.updated, "settings")

    def _apply(
        self,
        section: str,
        name: str,
        data: dict[str, Any],
        existing: dict[str, Any],
        create: Any,
        update: Any,
        result: ImportResult,
        overwrite: bool,
    ) -> None:
        try:
            current = existing.get(name.lower())
            if current is None:
                create(data)
                result.count(result.created, section)
            elif overwrite:
                update(current.id, data)
                result.count(result.updated, section)
            else:
                result.count(result.skipped, section)
        except InvalidConfigurationError as exc:
            result.errors.append(
                f"{section} '{name}': {_describe(exc.field_errors) or exc.message}"
            )
        except AppError as exc:
            result.errors.append(f"{section} '{name}': {exc.message}")


_SETTING_KEYS: Final = (
    SettingKey.UI_THEME,
    SettingKey.DEFAULT_MIN_DWELL,
    SettingKey.DEFAULT_MAX_DWELL,
    SettingKey.DEFAULT_TIMEOUT,
    SettingKey.SCREENSHOT_POLICY,
)


def _describe(errors: dict[str, str]) -> str:
    return "; ".join(f"{field}: {message}" for field, message in errors.items())


def _settings_problems(settings: dict[str, Any]) -> list[str]:
    """Only known keys with sensible values are accepted from a file."""
    problems: list[str] = []
    theme = settings.get(SettingKey.UI_THEME)
    if theme is not None and theme not in THEMES:
        problems.append(f"settings: theme must be one of {sorted(THEMES)}.")
    for key, low, high in (
        (SettingKey.DEFAULT_MIN_DWELL, 1, 3600),
        (SettingKey.DEFAULT_MAX_DWELL, 1, 3600),
        (SettingKey.DEFAULT_TIMEOUT, 5, 300),
    ):
        value = settings.get(key)
        if value is not None and not (
            isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
        ):
            problems.append(f"settings: {key} must be a whole number from {low} to {high}.")
    low_value, high_value = settings.get(SettingKey.DEFAULT_MIN_DWELL), settings.get(
        SettingKey.DEFAULT_MAX_DWELL
    )
    if isinstance(low_value, int) and isinstance(high_value, int) and low_value > high_value:
        problems.append("settings: the minimum dwell time exceeds the maximum.")
    policy = settings.get(SettingKey.SCREENSHOT_POLICY)
    if policy is not None and not (
        isinstance(policy, dict)
        and set(policy) <= set(POLICY_KEYS)
        and all(isinstance(v, bool) for v in policy.values())
    ):
        problems.append("settings: screenshot policy must map known names to true/false.")
    return problems
