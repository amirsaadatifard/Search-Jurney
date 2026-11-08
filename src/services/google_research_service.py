"""Google Research mode: real search data -> target detection -> approved direct visit.

Workflow::

    keyword -> search provider (official Google API) -> real results
            -> detect the target domain -> record the observed rank
            -> extract the target URL -> (user approves the domain)
            -> open that URL directly in Playwright -> dwell, scroll, screenshots

Nothing here clicks a search result, creates traffic on Google, rotates
addresses or bypasses protections. Results are never invented: if the provider
fails the error is raised to the caller and nothing is stored.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Final

import pandas as pd

from src.app.health import HealthCheckResult, HealthStatus, find_playwright_chromium
from src.automation.models import SessionPlan
from src.automation.plan_factory import PlanFactory
from src.database.models import ObservedResult, SearchObservation, TargetSite
from src.database.repositories import BrowserProfileRepository
from src.database.repositories.observation_repository import ObservationRepository
from src.database.session import Database
from src.domain.enums import Environment
from src.domain.exceptions import (
    EntityNotFoundError,
    InvalidConfigurationError,
    SafetyViolationError,
    TargetNotFoundError,
)
from src.logging_config import get_logger
from src.reports.common import safe_frame, validate_output_path
from src.search_providers import (
    ProviderCheck,
    SearchProvider,
    domain_matches,
    find_target,
    normalize_target_domain,
    result_domain,
)
from src.services.google_credentials import CredentialStatus, GoogleCredentialManager
from src.services.safety_service import DomainSafetyService, is_public_search_engine, normalize_url
from src.services.target_site_service import TargetSiteService

log = get_logger(__name__)

MAX_QUERY_LENGTH: Final = 200
MIN_RESULTS: Final = 1
MAX_RESULTS: Final = 100
INTERNET_PROBE: tuple[str, int] = ("www.googleapis.com", 443)
INTERNET_TIMEOUT_SECONDS: Final = 5.0
QUICK_OPEN_SECONDS: Final = 3


class GoogleResearchService:
    def __init__(
        self,
        database: Database,
        credentials: GoogleCredentialManager,
        provider: SearchProvider,
        target_sites: TargetSiteService,
        safety: DomainSafetyService,
        plans: PlanFactory,
        internet_probe: tuple[str, int] = INTERNET_PROBE,
    ) -> None:
        self._database = database
        self._credentials = credentials
        self._provider = provider
        self._sites = target_sites
        self._safety = safety
        self._plans = plans
        self._probe = internet_probe

    @property
    def provider_name(self) -> str:
        return self._provider.name

    # -- credentials ------------------------------------------------------------------- #
    @property
    def credentials(self) -> GoogleCredentialManager:
        return self._credentials

    def credential_status(self) -> CredentialStatus:
        return self._credentials.status()

    # -- search ------------------------------------------------------------------------- #
    def test_connection(self) -> ProviderCheck:
        """Ask the provider to confirm credentials and reachability (uses one query)."""
        return self._provider.check()

    def run_search(
        self,
        query: str,
        target_domain: str,
        language: str = "",
        country: str = "",
        count: int = 10,
    ) -> SearchObservation:
        """Search for real, detect the target and store the observation.

        Raises a ``Google*Error`` if the provider cannot answer; nothing is stored then.
        """
        text = " ".join(query.split())
        errors: dict[str, str] = {}
        if not text:
            errors["keyword"] = "Enter a keyword."
        elif len(text) > MAX_QUERY_LENGTH:
            errors["keyword"] = f"Use at most {MAX_QUERY_LENGTH} characters."
        if not MIN_RESULTS <= count <= MAX_RESULTS:
            errors["count"] = f"Use {MIN_RESULTS} to {MAX_RESULTS} results."
        domain = ""
        try:
            domain = normalize_target_domain(target_domain)
            if is_public_search_engine(domain):
                errors["target_domain"] = "A search engine cannot be the target site."
        except InvalidConfigurationError as exc:
            errors.update(exc.field_errors)
        if errors:
            raise InvalidConfigurationError("Some fields need attention.", field_errors=errors)

        results = self._provider.search(text, language, country, count)
        match = find_target(results, domain)
        with self._database.session_scope() as session:
            repo = ObservationRepository(session)
            observation = repo.add(
                SearchObservation(
                    query=text,
                    target_domain=domain,
                    target_found=match is not None,
                    target_position=match.position if match else None,
                    target_url=match.url if match else None,
                    provider=self._provider.name,
                    language=language,
                    country=country,
                    requested_results=count,
                    result_count=len(results),
                )
            )
            repo.add_results(
                observation,
                [
                    ObservedResult(
                        position=r.position,
                        title=r.title[:500],
                        domain=r.domain,
                        url=r.url[:2000],
                        snippet=r.snippet,
                        is_target=domain_matches(r.domain, domain),
                    )
                    for r in results
                ],
            )
            observation_id = observation.id
        log.info("observation_stored", id=observation_id, found=match is not None)
        return self.get_observation(observation_id)

    def get_observation(self, observation_id: int) -> SearchObservation:
        with self._database.session_scope() as session:
            observation = ObservationRepository(session).get_with_results(observation_id)
        if observation is None:
            raise EntityNotFoundError(f"Observation #{observation_id} does not exist.")
        return observation

    def export_observation_csv(self, observation_id: int, path: Path) -> Path:
        """Write the results of one observation to a CSV file (formula-safe)."""
        observation = self.get_observation(observation_id)
        target = validate_output_path(path, ".csv")
        frame = pd.DataFrame(
            [
                {
                    "position": r.position,
                    "title": r.title,
                    "domain": r.domain,
                    "url": r.url,
                    "snippet": r.snippet,
                    "target_match": r.is_target,
                    "query": observation.query,
                    "provider": observation.provider,
                    "observed_at": observation.created_at,
                }
                for r in observation.results
            ]
        )
        safe_frame(frame).to_csv(target, index=False, encoding="utf-8-sig")
        return target

    def list_observations(self, limit: int = 50) -> list[SearchObservation]:
        with self._database.session_scope() as session:
            return list(ObservationRepository(session).latest(limit))

    # -- target approval ------------------------------------------------------------------- #
    def approved_site_for(self, url_or_domain: str) -> TargetSite | None:
        """The enabled, approved target site that covers ``url_or_domain``, if any."""
        host = result_domain(url_or_domain) if "://" in url_or_domain else url_or_domain.lower()
        for site in self._sites.search():
            if site.is_automation_allowed and domain_matches(host, site.domain):
                return site
        return None

    def is_visit_allowed(self, url: str) -> bool:
        """Whether the safety service lets automation open exactly this URL."""
        return self._safety.is_allowed(url)

    def approve_target(self, observation_id: int) -> TargetSite:
        """Register and approve the observed target's site (the caller confirms ownership)."""
        observation = self.get_observation(observation_id)
        if not observation.target_found or not observation.target_url:
            raise TargetNotFoundError("The target was not found, so there is nothing to approve.")
        origin = normalize_url(observation.target_url).origin
        host = result_domain(observation.target_url)
        existing = next((s for s in self._sites.search() if s.base_url.rstrip("/") == origin), None)
        if existing is not None:
            self._sites.set_enabled(existing.id, True)
            self._sites.set_approved(existing.id, True)
            return existing
        name = f"Google Research - {host}"
        return self._sites.create(
            {
                "display_name": name[:120],
                "base_url": origin,
                "environment": Environment.CONTROLLED_TEST,
                "notes": "Registered from Google Research mode; the user confirmed authorisation.",
                "enabled": True,
                "approved_for_automation": True,
            }
        )

    # -- site test ---------------------------------------------------------------------------- #
    def plan_for(
        self, observation_id: int, browser_profile_id: int, quick: bool = False
    ) -> SessionPlan:
        """Build the one-session plan that opens the observed target URL directly."""
        observation = self.get_observation(observation_id)
        if not observation.target_found or not observation.target_url:
            raise TargetNotFoundError(
                "The target domain was not found in the results, so there is no page to open. "
                "Run a search that finds it first."
            )
        try:
            self._safety.check_url(observation.target_url)
        except SafetyViolationError as exc:
            raise SafetyViolationError(
                "The target site is not approved for automation. Approve it first (only for "
                f"sites you own or are authorised to test). {exc.message}",
                reason=exc.context.get("reason"),
            ) from exc
        with self._database.session_scope() as session:
            profile = BrowserProfileRepository(session).require(browser_profile_id)
        return self._plans.direct_visit_plan(
            observation, profile, quick_seconds=QUICK_OPEN_SECONDS if quick else None
        )

    # -- system checks --------------------------------------------------------------------------- #
    def system_checks(
        self, target_domain: str = "", probe_provider: bool = True
    ) -> list[HealthCheckResult]:
        """The checks shown before a presentation. Network checks may take a few seconds."""
        checks = [self._check_internet(), self._check_credentials()]
        checks.append(self._check_provider(probe_provider))
        checks.append(self._check_database())
        checks.append(self._check_playwright())
        checks.append(self._check_chromium())
        checks.append(self._check_allowlist(target_domain))
        return checks

    def _check_internet(self) -> HealthCheckResult:
        name = "Internet connection"
        started = time.perf_counter()
        try:
            with socket.create_connection(self._probe, timeout=INTERNET_TIMEOUT_SECONDS):
                pass
        except OSError as exc:
            return HealthCheckResult(
                name, HealthStatus.ERROR, f"Cannot reach {self._probe[0]}: {exc.strerror or exc}"
            )
        ms = round((time.perf_counter() - started) * 1000)
        return HealthCheckResult(name, HealthStatus.OK, f"{self._probe[0]} reachable ({ms} ms)")

    def _check_credentials(self) -> HealthCheckResult:
        name = "Google provider credentials"
        status = self.credential_status()
        if status.complete:
            return HealthCheckResult(
                name,
                HealthStatus.OK,
                f"API key ({status.api_key_source}) and search engine id are set",
            )
        missing = [
            label
            for label, ok in (
                ("API key", status.api_key_set),
                ("search engine id", bool(status.engine_id)),
            )
            if not ok
        ]
        return HealthCheckResult(name, HealthStatus.ERROR, f"Missing: {', '.join(missing)}")

    def _check_provider(self, probe: bool) -> HealthCheckResult:
        name = "Google provider availability"
        if not self.credential_status().complete:
            return HealthCheckResult(
                name, HealthStatus.WARNING, "Not checked: credentials are incomplete"
            )
        if not probe:
            return HealthCheckResult(
                name, HealthStatus.WARNING, "Not checked (uses one query of the quota)"
            )
        outcome = self.test_connection()
        if outcome.ok:
            return HealthCheckResult(
                name, HealthStatus.OK, f"{outcome.detail} ({outcome.latency_ms} ms)"
            )
        return HealthCheckResult(name, HealthStatus.ERROR, outcome.detail)

    def _check_database(self) -> HealthCheckResult:
        try:
            self._database.ping()
        except Exception as exc:
            return HealthCheckResult("Database", HealthStatus.ERROR, str(exc))
        return HealthCheckResult("Database", HealthStatus.OK, "SQLite connection established")

    @staticmethod
    def _check_playwright() -> HealthCheckResult:
        import importlib.util

        if importlib.util.find_spec("playwright") is None:
            return HealthCheckResult(
                "Playwright", HealthStatus.ERROR, "Package missing: pip install playwright"
            )
        return HealthCheckResult("Playwright", HealthStatus.OK, "Package installed")

    @staticmethod
    def _check_chromium() -> HealthCheckResult:
        if find_playwright_chromium() is None:
            return HealthCheckResult(
                "Chromium", HealthStatus.ERROR, "Run: python -m playwright install chromium"
            )
        return HealthCheckResult("Chromium", HealthStatus.OK, "Browser downloaded")

    def _check_allowlist(self, target_domain: str) -> HealthCheckResult:
        name = "Target domain allowlist"
        if not target_domain.strip():
            return HealthCheckResult(
                name, HealthStatus.WARNING, "Enter a target domain to check it"
            )
        try:
            domain = normalize_target_domain(target_domain)
        except InvalidConfigurationError as exc:
            return HealthCheckResult(
                name, HealthStatus.ERROR, exc.field_errors.get("target_domain", exc.message)
            )
        site = self.approved_site_for(domain)
        if site is None:
            return HealthCheckResult(
                name,
                HealthStatus.WARNING,
                f"{domain} is not approved yet (approve it before the site test)",
            )
        return HealthCheckResult(
            name, HealthStatus.OK, f"{domain} is approved ({site.display_name})"
        )


__all__ = ["CredentialStatus", "GoogleResearchService"]
