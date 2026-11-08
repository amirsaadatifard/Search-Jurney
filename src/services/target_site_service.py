"""Target sites: the user-managed part of the automation allowlist."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.database.models import TargetSite
from src.database.repositories import ScenarioRepository, TargetSiteRepository
from src.database.session import Database
from src.domain.enums import Environment
from src.domain.exceptions import (
    EntityInUseError,
    InvalidConfigurationError,
    SafetyViolationError,
)
from src.domain.schemas import TargetSiteInput, validate_input
from src.logging_config import get_logger
from src.services.safety_service import DomainSafetyService, NormalizedUrl

log = get_logger(__name__)


class TargetSiteService:
    def __init__(self, database: Database, safety: DomainSafetyService) -> None:
        self._database = database
        self._safety = safety

    def search(
        self,
        text: str = "",
        environment: Environment | None = None,
        approved: bool | None = None,
    ) -> list[TargetSite]:
        with self._database.session_scope() as session:
            return list(TargetSiteRepository(session).search(text, environment, approved))

    def create(self, data: Mapping[str, Any]) -> TargetSite:
        values, url = self._validate(data)
        with self._database.session_scope() as session:
            repo = TargetSiteRepository(session)
            if repo.get_by_name(values.display_name) is not None:
                raise _duplicate(values.display_name)
            site = repo.add(
                TargetSite(
                    **values.model_dump(exclude={"base_url"}), base_url=url.url, domain=url.host
                )
            )
        log.info("target_site_created", domain=url.host, approved=values.approved_for_automation)
        return site

    def update(self, site_id: int, data: Mapping[str, Any]) -> TargetSite:
        values, url = self._validate(data)
        with self._database.session_scope() as session:
            repo = TargetSiteRepository(session)
            site = repo.require(site_id)
            existing = repo.get_by_name(values.display_name)
            if existing is not None and existing.id != site_id:
                raise _duplicate(values.display_name)
            for name, value in values.model_dump(exclude={"base_url"}).items():
                setattr(site, name, value)
            site.base_url = url.url
            site.domain = url.host
            return site

    def set_enabled(self, site_id: int, enabled: bool) -> None:
        with self._database.session_scope() as session:
            TargetSiteRepository(session).require(site_id).enabled = enabled

    def set_approved(self, site_id: int, approved: bool) -> None:
        """Grant or revoke approval. Granting re-runs the safety validation."""
        with self._database.session_scope() as session:
            site = TargetSiteRepository(session).require(site_id)
            if approved:
                self._safety.validate_target_candidate(site.base_url, site.environment)
            site.approved_for_automation = approved
            log.info("target_site_approval_changed", domain=site.domain, approved=approved)

    def delete(self, site_id: int) -> None:
        with self._database.session_scope() as session:
            repo = TargetSiteRepository(session)
            site = repo.require(site_id)
            in_use = ScenarioRepository(session).count_using_target_site(site_id)
            if in_use:
                raise EntityInUseError(
                    f"'{site.display_name}' is used by {in_use} scenario(s). "
                    "Change or delete those scenarios first."
                )
            repo.delete(site)

    def _validate(self, data: Mapping[str, Any]) -> tuple[TargetSiteInput, NormalizedUrl]:
        values = validate_input(TargetSiteInput, data)
        try:
            url = self._safety.validate_target_candidate(values.base_url, values.environment)
        except SafetyViolationError as exc:
            raise InvalidConfigurationError(
                "The address cannot be used as a target site.",
                field_errors={"base_url": exc.message},
            ) from exc
        return values, url


def _duplicate(name: str) -> InvalidConfigurationError:
    return InvalidConfigurationError(
        "Duplicate name.",
        field_errors={"display_name": f"A site named '{name}' already exists."},
    )
