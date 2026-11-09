"""Dependency container: the single place where long-lived objects are held."""

from __future__ import annotations

from dataclasses import dataclass

from src.app.config import AppConfig
from src.app.health import HealthReport, StartupHealthService
from src.app.paths import AppPaths
from src.automation.run_service import AutomationRunService
from src.database.migrator import DatabaseMigrator
from src.database.session import Database
from src.local_servers.server_manager import LocalServerManager
from src.logging_config import UiLogBuffer
from src.networking.credentials import CredentialStore
from src.services.backup_service import BackupService
from src.services.browser_profile_service import BrowserProfileService
from src.services.config_transfer_service import ConfigTransferService
from src.services.dashboard_service import DashboardService
from src.services.demo_service import DemoService
from src.services.keyword_service import KeywordService
from src.services.proxy_service import ProxyService
from src.services.report_service import ReportService
from src.services.safety_service import DomainSafetyService
from src.services.scenario_service import ScenarioService
from src.services.session_service import SessionService
from src.services.settings_service import SettingsService
from src.services.target_site_service import TargetSiteService


@dataclass(slots=True)
class AppContainer:
    """Wired application services, created by :func:`src.app.bootstrap.bootstrap`.

    The GUI receives this object and passes the individual services down to the
    pages that need them; nothing reaches for module-level singletons.
    """

    config: AppConfig
    paths: AppPaths
    database: Database
    log_buffer: UiLogBuffer
    settings: SettingsService
    health_service: StartupHealthService
    health_report: HealthReport
    migrator: DatabaseMigrator
    safety: DomainSafetyService
    keywords: KeywordService
    target_sites: TargetSiteService
    browser_profiles: BrowserProfileService
    scenarios: ScenarioService
    servers: LocalServerManager
    runs: AutomationRunService
    sessions: SessionService
    reports: ReportService
    backups: BackupService
    transfer: ConfigTransferService
    demo: DemoService
    dashboard: DashboardService
    credentials: CredentialStore
    proxies: ProxyService
