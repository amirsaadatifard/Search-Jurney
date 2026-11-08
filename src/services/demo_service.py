"""One-click demo: make sure the bundled demo data is usable, then pick its scenario."""

from __future__ import annotations

from src.database.models import TargetSite
from src.database.repositories import TargetSiteRepository
from src.database.session import Database
from src.logging_config import get_logger
from src.services.first_run_service import DEMO_SCENARIO_NAME, DEMO_SITE_NAME, FirstRunService
from src.services.scenario_service import ScenarioService
from src.services.target_site_service import TargetSiteService

log = get_logger(__name__)


class DemoService:
    def __init__(
        self,
        database: Database,
        first_run: FirstRunService,
        scenarios: ScenarioService,
        target_sites: TargetSiteService,
    ) -> None:
        self._database = database
        self._first_run = first_run
        self._scenarios = scenarios
        self._target_sites = target_sites

    def prepare(self) -> int:
        """Return the id of a runnable demo scenario, repairing the demo data if needed.

        Only the bundled *local* demo site is re-approved here; a user's own sites
        are never touched.
        """
        self._first_run.ensure_demo_data()
        with self._database.session_scope() as session:
            site = TargetSiteRepository(session).get_by_name(DEMO_SITE_NAME)
            assert isinstance(site, TargetSite)
            site_id, usable = site.id, site.is_automation_allowed
        if not usable:
            self._target_sites.set_enabled(site_id, True)
            self._target_sites.set_approved(site_id, True)
            log.info("demo_site_repaired")
        scenario = next(s for s in self._scenarios.search() if s.name == DEMO_SCENARIO_NAME)
        if not scenario.enabled:
            self._scenarios.set_enabled(scenario.id, True)
        return int(scenario.id)
