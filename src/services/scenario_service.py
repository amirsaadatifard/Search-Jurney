"""Scenario management and validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from src.app.constants import DEFAULT_MAX_DWELL_SECONDS, DEFAULT_MIN_DWELL_SECONDS
from src.database.models import BrowserProfile, ProxyProfile, Scenario, TargetSite
from src.database.repositories import (
    BrowserProfileRepository,
    KeywordRepository,
    ProxyProfileRepository,
    ScenarioRepository,
    TargetSiteRepository,
)
from src.database.repositories.scenario_repository import DEFAULT_SCENARIO_SORT
from src.database.session import Database
from src.domain.exceptions import InvalidConfigurationError
from src.domain.schemas import NAME_MAX, ScenarioInput, validate_input
from src.logging_config import get_logger
from src.services.settings_service import SettingsService

log = get_logger(__name__)

# Columns copied when a scenario is duplicated (everything except identity/timestamps).
_COPIED_FIELDS: tuple[str, ...] = tuple(
    name for name in ScenarioInput.model_fields if name != "name"
)


@dataclass(frozen=True, slots=True)
class ScenarioFormOptions:
    """Choices the scenario editor offers in its drop-downs."""

    target_sites: list[TargetSite]
    browser_profiles: list[BrowserProfile]
    proxy_profiles: list[ProxyProfile]
    keywords: list[str]
    default_min_dwell: int = DEFAULT_MIN_DWELL_SECONDS
    default_max_dwell: int = DEFAULT_MAX_DWELL_SECONDS


class ScenarioService:
    def __init__(self, database: Database, settings: SettingsService | None = None) -> None:
        self._database = database
        self._settings = settings

    # -- queries ---------------------------------------------------------------- #
    def search(
        self,
        text: str = "",
        enabled: bool | None = None,
        target_site_id: int | None = None,
        sort_by: str = DEFAULT_SCENARIO_SORT,
        descending: bool = False,
    ) -> list[Scenario]:
        with self._database.session_scope() as session:
            return list(
                ScenarioRepository(session).search(
                    text, enabled, target_site_id, sort_by, descending
                )
            )

    def get(self, scenario_id: int) -> Scenario:
        with self._database.session_scope() as session:
            return ScenarioRepository(session).require(scenario_id)

    def form_options(self) -> ScenarioFormOptions:
        defaults = self._settings.automation_defaults() if self._settings else None
        with self._database.session_scope() as session:
            return ScenarioFormOptions(
                target_sites=list(TargetSiteRepository(session).search()),
                browser_profiles=list(BrowserProfileRepository(session).list_sorted()),
                proxy_profiles=list(ProxyProfileRepository(session).list_sorted(enabled_only=True)),
                keywords=[kw.text for kw in KeywordRepository(session).search(enabled=True)],
                default_min_dwell=(
                    defaults.min_dwell_seconds if defaults else DEFAULT_MIN_DWELL_SECONDS
                ),
                default_max_dwell=(
                    defaults.max_dwell_seconds if defaults else DEFAULT_MAX_DWELL_SECONDS
                ),
            )

    # -- commands ------------------------------------------------------------------- #
    def create(self, data: Mapping[str, Any], require_approved: bool = True) -> Scenario:
        """Create a scenario. ``require_approved=False`` (used by imports) allows a target
        that is not approved yet; such a scenario is stored disabled.
        """
        values = validate_input(ScenarioInput, data)
        with self._database.session_scope() as session:
            allowed = self._check_references(session, values, None, require_approved)
            fields = values.model_dump() | ({} if allowed else {"enabled": False})
            scenario = ScenarioRepository(session).add(Scenario(**fields))
            session.refresh(scenario)
        log.info("scenario_created", name=values.name)
        return scenario

    def update(
        self, scenario_id: int, data: Mapping[str, Any], require_approved: bool = True
    ) -> Scenario:
        values = validate_input(ScenarioInput, data)
        with self._database.session_scope() as session:
            scenario = ScenarioRepository(session).require(scenario_id)
            allowed = self._check_references(session, values, scenario_id, require_approved)
            fields = values.model_dump() | ({} if allowed else {"enabled": False})
            for name, value in fields.items():
                setattr(scenario, name, value)
            session.flush()
            session.refresh(scenario)
            return scenario

    def duplicate(self, scenario_id: int) -> Scenario:
        """Create a copy with a unique "(copy)" name. The copy starts disabled."""
        with self._database.session_scope() as session:
            repo = ScenarioRepository(session)
            original = repo.require(scenario_id)
            copy = Scenario(
                name=_copy_name(repo, original.name),
                **{name: getattr(original, name) for name in _COPIED_FIELDS},
            )
            copy.enabled = False
            repo.add(copy)
            session.refresh(copy)
            return copy

    def set_enabled(self, scenario_id: int, enabled: bool) -> None:
        with self._database.session_scope() as session:
            scenario = ScenarioRepository(session).require(scenario_id)
            if enabled and not scenario.target_site.is_automation_allowed:
                raise InvalidConfigurationError(
                    f"'{scenario.target_site.display_name}' is not approved for automation, "
                    "so this scenario cannot be enabled."
                )
            scenario.enabled = enabled

    def delete(self, scenario_id: int) -> None:
        with self._database.session_scope() as session:
            repo = ScenarioRepository(session)
            repo.delete(repo.require(scenario_id))

    # -- validation ------------------------------------------------------------------- #
    @staticmethod
    def _check_references(
        session: Session,
        values: ScenarioInput,
        scenario_id: int | None,
        require_approved: bool = True,
    ) -> bool:
        """Rules that need the database: uniqueness, existence and the allowlist.

        Returns whether the target site is currently allowed for automation.
        """
        site_allowed = False
        errors: dict[str, str] = {}

        existing = ScenarioRepository(session).get_by_name(values.name)
        if existing is not None and existing.id != scenario_id:
            errors["name"] = f"A scenario named '{values.name}' already exists."

        site = TargetSiteRepository(session).get(values.target_site_id)
        if site is None:
            errors["target_site_id"] = "Choose a target site."
        elif site.is_automation_allowed:
            site_allowed = True
        elif require_approved:
            errors["target_site_id"] = (
                f"'{site.display_name}' is not on the allowlist. Enable and approve it "
                "under Target Sites first."
            )

        if BrowserProfileRepository(session).get(values.browser_profile_id) is None:
            errors["browser_profile_id"] = "Choose a browser profile."

        if values.proxy_profile_id is not None:
            proxy = ProxyProfileRepository(session).get(values.proxy_profile_id)
            if proxy is None:
                errors["proxy_profile_id"] = "The selected proxy profile no longer exists."
            elif not proxy.enabled:
                errors["proxy_profile_id"] = f"Proxy profile '{proxy.name}' is disabled."

        if errors:
            raise InvalidConfigurationError("Some fields need attention.", field_errors=errors)
        return site_allowed


def _copy_name(repo: ScenarioRepository, original: str) -> str:
    """Return ``"<name> (copy)"`` or ``"<name> (copy N)"``, whichever is free."""
    attempt = 1
    while True:
        suffix = " (copy)" if attempt == 1 else f" (copy {attempt})"
        candidate = original[: NAME_MAX - len(suffix)] + suffix
        if repo.get_by_name(candidate) is None:
            return candidate
        attempt += 1
