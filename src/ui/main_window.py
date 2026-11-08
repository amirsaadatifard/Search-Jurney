"""Main application window: sidebar on the left, stacked pages on the right."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QWidget

from src.app.constants import (
    APP_NAME,
    APP_VERSION,
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from src.app.container import AppContainer
from src.automation.models import RunSummary
from src.domain.exceptions import AppError
from src.logging_config import get_logger
from src.services.settings_service import SettingKey
from src.ui.navigation import PageKey, all_nav_items
from src.ui.pages.about_page import AboutPage
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.pages.browser_profiles_page import BrowserProfilesPage
from src.ui.pages.dashboard_page import DashboardPage
from src.ui.pages.google_research_page import GoogleResearchPage
from src.ui.pages.keywords_page import KeywordsPage
from src.ui.pages.live_view_page import LiveViewPage
from src.ui.pages.logs_page import LogsPage
from src.ui.pages.proxies_page import ProxiesPage
from src.ui.pages.reports_page import ReportsPage
from src.ui.pages.runner_page import RunnerPage
from src.ui.pages.scenarios_page import ScenariosPage
from src.ui.pages.sessions_page import SessionsPage
from src.ui.pages.settings_page import SettingsPage
from src.ui.pages.targets_page import TargetsPage
from src.ui.run_controller import RunController
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.sidebar import Sidebar
from src.ui.widgets.toast import Notifier

log = get_logger(__name__)

PageFactory = Callable[[PageContext], BasePage]

PAGE_FACTORIES: dict[PageKey, PageFactory] = {
    PageKey.DASHBOARD: DashboardPage,
    PageKey.SCENARIOS: ScenariosPage,
    PageKey.KEYWORDS: KeywordsPage,
    PageKey.TARGETS: TargetsPage,
    PageKey.PROXIES: ProxiesPage,
    PageKey.BROWSER_PROFILES: BrowserProfilesPage,
    PageKey.GOOGLE_RESEARCH: GoogleResearchPage,
    PageKey.RUNNER: RunnerPage,
    PageKey.LIVE_VIEW: LiveViewPage,
    PageKey.SESSIONS: SessionsPage,
    PageKey.LOGS: LogsPage,
    PageKey.REPORTS: ReportsPage,
    PageKey.SETTINGS: SettingsPage,
    PageKey.ABOUT: AboutPage,
}


class MainWindow(QMainWindow):
    """Hosts the navigation and owns the page widgets."""

    def __init__(self, container: AppContainer, theme: ThemeManager) -> None:
        super().__init__()
        self._container = container
        self._theme = theme
        self._pages: dict[PageKey, BasePage] = {}

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)

        self._sidebar = Sidebar(theme)
        self._stack = QStackedWidget()
        self._stack.setObjectName("PageStack")

        self.notifier = Notifier(self)
        self.runs = RunController(container.runs, container.demo.prepare, self)
        context = PageContext(
            container=container,
            theme=theme,
            notifier=self.notifier,
            runs=self.runs,
            navigate=self.show_page,
            apply_theme=self.apply_theme_mode,
            start_demo=self.start_demo,
        )
        for item in all_nav_items():
            page = PAGE_FACTORIES[item.key](context)
            self._pages[item.key] = page
            self._stack.addWidget(page)

        root = QWidget()
        root.setObjectName("AppRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._sidebar)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(root)

        self._build_status_bar()
        self._sidebar.page_requested.connect(self._on_page_requested)
        self._sidebar.theme_mode_requested.connect(self._on_theme_mode_requested)
        self.runs.changed.connect(self._on_run_changed)
        self.runs.finished.connect(self._on_run_finished)
        self.show_page(PageKey.DASHBOARD)

    # -- navigation ----------------------------------------------------------- #
    def show_page(self, key: PageKey) -> None:
        """Switch to the page identified by ``key``."""
        page = self._pages[key]
        self._stack.setCurrentWidget(page)
        self._sidebar.select(key)
        page.on_activated()

    def current_page_key(self) -> PageKey:
        current = self._stack.currentWidget()
        return next(key for key, page in self._pages.items() if page is current)

    def page(self, key: PageKey) -> BasePage:
        return self._pages[key]

    def _on_page_requested(self, key: str) -> None:
        self.show_page(PageKey(key))

    # -- theme ----------------------------------------------------------------- #
    def _on_theme_mode_requested(self, mode: str) -> None:
        self.apply_theme_mode(ThemeMode.parse(mode))

    def apply_theme_mode(self, mode: ThemeMode) -> None:
        """Switch the theme, update the sidebar buttons and remember the choice."""
        self._theme.set_mode(mode)
        self._sidebar.set_theme_mode(mode)
        self._container.settings.set(SettingKey.UI_THEME, mode.value)
        log.info("theme_changed", mode=mode.value)

    def start_demo(self) -> None:
        """One-click demo: prepare the demo data, run one cycle and show it live."""
        try:
            self.runs.start_demo()
        except AppError as exc:
            self.notifier.error(exc.message)
            return
        self.show_page(PageKey.LIVE_VIEW)

    # -- chrome ---------------------------------------------------------------- #
    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        bar.setContentsMargins(12, 0, 12, 0)
        self._status_message = QLabel("Ready")
        health = QLabel(f"Environment: {self._container.health_report.summary()}")
        version = QLabel(f"v{APP_VERSION}")
        bar.addWidget(self._status_message, 1)
        bar.addPermanentWidget(health)
        bar.addPermanentWidget(version)

    def set_status_message(self, text: str) -> None:
        self._status_message.setText(text)

    # -- run feedback ---------------------------------------------------------- #
    def _on_run_changed(self) -> None:
        runs = self.runs
        if runs.is_active:
            self.set_status_message(
                f"Running: {runs.scenario_name} · {runs.iteration}/{runs.total_iterations} · "
                f"{runs.step}"
            )
        elif not runs.has_run:
            self.set_status_message("Ready")

    def _on_run_finished(self, summary: RunSummary) -> None:
        text = (
            f"Run finished: {summary.completed} completed, {summary.target_not_found} not found, "
            f"{summary.failed} failed, {summary.stopped} stopped."
        )
        self.set_status_message(text)
        if summary.failed:
            self.notifier.error(text)
        else:
            self.notifier.success(text)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.runs.is_active and not ConfirmDialog.ask(
            self,
            "Quit while a run is in progress?",
            "The current run will be stopped safely before the application closes.",
            confirm_text="Stop and quit",
            danger=True,
        ):
            event.ignore()
            return
        log.info("main_window_closing")
        super().closeEvent(event)
