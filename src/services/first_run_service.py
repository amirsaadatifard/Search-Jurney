"""First-run experience: demo data so the project can be tried immediately."""

from __future__ import annotations

from typing import Final

from src.app.config import AppConfig
from src.app.constants import LOOPBACK_HOST
from src.database.models import BrowserProfile, Keyword, Scenario, TargetSite
from src.database.repositories import (
    BrowserProfileRepository,
    KeywordRepository,
    ScenarioRepository,
    TargetSiteRepository,
)
from src.database.session import Database
from src.domain.enums import Environment
from src.logging_config import get_logger
from src.services.settings_service import SettingKey, SettingsService

log = get_logger(__name__)

DEMO_SCENARIO_NAME: Final = "Demo - Sample site journey"
DEMO_SITE_NAME: Final = "Local Demo Site"
DEMO_BROWSER_NAME: Final = "Default Desktop"
DEMO_KEYWORD: Final = "handmade ceramic mugs"
DEMO_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (DEMO_KEYWORD, ("demo", "products")),
    ("pottery workshop near me", ("demo", "local")),
    ("how to glaze stoneware", ("demo", "article")),
    ("ceramic studio opening hours", ("demo", "contact")),
    ("wheel throwing for beginners", ("demo", "article")),
)


class FirstRunService:
    """Creates the demo browser profile, target site, keywords and scenario once.

    The seeded journey stays runnable with no network at all: its target is the
    sample website the application serves itself. A real Google journey needs a
    site the user owns, approves and (for the ranking step) credentials.
    """

    def __init__(self, database: Database, settings: SettingsService, config: AppConfig) -> None:
        self._database = database
        self._settings = settings
        self._config = config

    def run_if_needed(self) -> bool:
        """Seed demo data on the first launch. Returns ``True`` if it ran."""
        if self._settings.get(SettingKey.FIRST_RUN_COMPLETED, False):
            return False
        self.ensure_demo_data()
        self._settings.set(SettingKey.FIRST_RUN_COMPLETED, True)
        log.info("first_run_completed")
        return True

    def ensure_demo_data(self) -> None:
        """Insert each demo record unless one with the same name already exists.

        Also keeps the bundled demo site pointing at the current test-site port.
        """
        with self._database.session_scope() as session:
            browsers = BrowserProfileRepository(session)
            browser = browsers.get_by_name(DEMO_BROWSER_NAME) or browsers.add(
                BrowserProfile(name=DEMO_BROWSER_NAME)
            )

            sites = TargetSiteRepository(session)
            site = sites.get_by_name(DEMO_SITE_NAME) or sites.add(
                TargetSite(
                    display_name=DEMO_SITE_NAME,
                    base_url=f"http://{LOOPBACK_HOST}:{self._config.target_port}/",
                    domain=LOOPBACK_HOST,
                    environment=Environment.LOCAL,
                    notes="Sample website bundled with the project. Safe for demonstrations.",
                    enabled=True,
                    approved_for_automation=True,
                )
            )

            demo_url = f"http://{LOOPBACK_HOST}:{self._config.target_port}/"
            if site.base_url != demo_url:  # the port was changed in the configuration
                site.base_url = demo_url

            keywords = KeywordRepository(session)
            for text, tags in DEMO_KEYWORDS:
                if keywords.get_by_text(text) is None:
                    keywords.add(Keyword(text=text, tags=list(tags)))

            scenarios = ScenarioRepository(session)
            if scenarios.get_by_name(DEMO_SCENARIO_NAME) is None:
                scenarios.add(
                    Scenario(
                        name=DEMO_SCENARIO_NAME,
                        description=(
                            "Opens the bundled sample website and scrolls through it. Google "
                            "cannot list a loopback address, so this journey claims no ranking; "
                            "point a scenario at a site you own to see its real position first."
                        ),
                        keyword=DEMO_KEYWORD,
                        target_site=site,
                        browser_profile=browser,
                        min_dwell_seconds=8,
                        max_dwell_seconds=15,
                        iterations=2,
                        delay_between_iterations=2,
                    )
                )
