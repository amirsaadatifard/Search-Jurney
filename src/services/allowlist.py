"""Database-backed allowlist provider for :class:`DomainSafetyService`."""

from __future__ import annotations

from src.database.repositories import TargetSiteRepository
from src.database.session import Database
from src.domain.exceptions import SafetyViolationError
from src.logging_config import get_logger
from src.services.safety_service import AllowedOrigin, normalize_url

log = get_logger(__name__)


class DatabaseAllowlist:
    """Reads the allowlist fresh from the database on every call.

    No caching: revoking a site's approval must take effect on the very next
    safety check, including in the middle of a running session.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def __call__(self) -> list[AllowedOrigin]:
        with self._database.session_scope() as session:
            base_urls = [
                site.base_url for site in TargetSiteRepository(session).list_automation_allowed()
            ]
        origins: list[AllowedOrigin] = []
        for base_url in base_urls:
            try:
                origins.append(AllowedOrigin.from_url(normalize_url(base_url)))
            except SafetyViolationError:
                # Stored URLs were validated on save; a bad one means manual tampering.
                log.warning("allowlist_entry_ignored", base_url=base_url)
        return origins
