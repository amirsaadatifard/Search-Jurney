"""Application bootstrap and shutdown.

``bootstrap`` contains no Qt code so the whole service graph can be created in
tests and command-line tools without a display. The search scope the journeys run
under is wired here: it is the only origin besides the approved target sites that
a browser session may ever open.
"""

from __future__ import annotations

from src.app.config import AppConfig
from src.app.constants import APP_NAME, APP_VERSION
from src.app.container import AppContainer
from src.app.health import StartupHealthService
from src.app.paths import PROJECT_ROOT, AppPaths
from src.automation.run_service import AutomationRunService
from src.automation.search_walk import GoogleSearchWalk
from src.database.base import utcnow
from src.database.migrator import DatabaseMigrator
from src.database.repositories import SessionRepository
from src.database.session import Database
from src.local_servers.server_manager import LocalServerManager
from src.logging_config import configure_logging, get_logger
from src.logging_config.setup import shutdown_logging
from src.networking.credentials import CredentialStore, KeyringCredentialStore
from src.services.allowlist import DatabaseAllowlist
from src.services.backup_service import BackupService
from src.services.browser_profile_service import BrowserProfileService
from src.services.config_transfer_service import ConfigTransferService
from src.services.dashboard_service import DashboardService
from src.services.demo_service import DemoService
from src.services.first_run_service import FirstRunService
from src.services.keyword_service import KeywordService
from src.services.proxy_service import ProxyService
from src.services.report_service import ReportService
from src.services.safety_service import DomainSafetyService, SearchScope
from src.services.scenario_service import ScenarioService
from src.services.session_service import SessionService
from src.services.settings_service import SettingsService
from src.services.target_site_service import TargetSiteService

log = get_logger(__name__)


def bootstrap(
    config: AppConfig | None = None,
    paths: AppPaths | None = None,
    credentials: CredentialStore | None = None,
    search_scope: SearchScope | None = None,
    search_walk: GoogleSearchWalk | None = None,
) -> AppContainer:
    """Create directories, logging, database and services; return the container.

    Args:
        config: Explicit configuration (tests). Defaults to the environment.
        paths: Explicit paths (tests). Defaults to ``config.data_dir``.
        credentials: Secret store (tests pass an in-memory one). Defaults to the OS store.
        search_scope: The pages a journey may browse while it searches (a test points
            it at a local fixture that speaks Google's markup; production uses
            Google, and nothing else).
        search_walk: The search step itself, injectable so a test can hand the
            runner a walk over a fabricated results page.
    """
    if config is None:
        config = AppConfig.from_environment(env_file=PROJECT_ROOT / ".env")
    if paths is None:
        paths = AppPaths.from_config(config)

    paths.ensure_directories()
    log_buffer = configure_logging(paths.log_file, config.log_level)
    log.info(
        "application_starting", name=APP_NAME, version=APP_VERSION, data_dir=str(paths.data_dir)
    )

    database = Database.for_file(paths.database_file)
    migrator = DatabaseMigrator(database, paths.migrations_dir)
    migrator.upgrade()
    _close_interrupted_sessions(database)

    settings = SettingsService(database)
    FirstRunService(database, settings, config).run_if_needed()

    # Every origin a journey may load must be an approved target site; the
    # application keeps no server of its own inside the allowlist, and the only
    # other pages a session may open are the ones in ``search_scope``.
    safety = DomainSafetyService(
        allowlist_provider=DatabaseAllowlist(database), search_scope=search_scope
    )
    servers = LocalServerManager(config.target_port)
    scenarios = ScenarioService(database, settings)
    credentials = credentials or KeyringCredentialStore()
    keywords = KeywordService(database)
    target_sites = TargetSiteService(database, safety)
    browser_profiles = BrowserProfileService(database)
    proxies = ProxyService(database, credentials)
    first_run = FirstRunService(database, settings, config)
    runs = AutomationRunService(
        database,
        safety,
        paths,
        settings,
        servers,
        scenarios,
        credentials,
        search_walk=search_walk,
    )
    health_service = StartupHealthService(config, paths, database, servers)
    health_report = health_service.run()
    log.info("startup_health", summary=health_report.summary(), worst=health_report.worst.value)

    return AppContainer(
        config=config,
        paths=paths,
        database=database,
        log_buffer=log_buffer,
        settings=settings,
        health_service=health_service,
        health_report=health_report,
        migrator=migrator,
        safety=safety,
        keywords=keywords,
        target_sites=target_sites,
        browser_profiles=browser_profiles,
        scenarios=scenarios,
        servers=servers,
        runs=runs,
        sessions=SessionService(database, paths),
        reports=ReportService(database),
        dashboard=DashboardService(database),
        credentials=credentials,
        proxies=proxies,
        backups=BackupService(database, paths, migrator, lambda: runs.is_running),
        transfer=ConfigTransferService(
            settings, keywords, target_sites, browser_profiles, proxies, scenarios
        ),
        demo=DemoService(database, first_run, scenarios, target_sites),
    )


def _close_interrupted_sessions(database: Database) -> None:
    """Mark sessions a previous crash left in ``RUNNING`` as failed."""
    with database.session_scope() as session:
        corrected = SessionRepository(session).mark_interrupted_sessions(utcnow())
    if corrected:
        log.warning("interrupted_sessions_closed", count=corrected)


def shutdown(container: AppContainer) -> None:
    """Release resources in reverse order of creation."""
    log.info("application_stopping")
    container.runs.shutdown()
    container.servers.stop_all()
    container.database.dispose()
    shutdown_logging()
