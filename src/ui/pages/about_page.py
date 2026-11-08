"""About page: project information, safety scope and startup health checks."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.constants import APP_DESCRIPTION, APP_NAME, APP_ORGANIZATION, APP_VERSION
from src.app.health import HealthReport, HealthStatus
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.widgets.card import Card
from src.ui.widgets.status_badge import BadgeTone, StatusBadge

_TONE_BY_STATUS: dict[HealthStatus, BadgeTone] = {
    HealthStatus.OK: BadgeTone.SUCCESS,
    HealthStatus.WARNING: BadgeTone.WARNING,
    HealthStatus.ERROR: BadgeTone.DANGER,
}
_LABEL_BY_STATUS: dict[HealthStatus, str] = {
    HealthStatus.OK: "OK",
    HealthStatus.WARNING: "Warning",
    HealthStatus.ERROR: "Error",
}
_SAFETY_POINTS: tuple[str, ...] = (
    "Automation runs only against localhost and explicitly approved test sites.",
    "Public search engines are blocked and cannot be added to the allowlist.",
    "Redirects that leave the approved domain abort the session.",
    "No CAPTCHA solving, fingerprint spoofing, IP rotation or anti-bot evasion is implemented.",
)


class AboutPage(BasePage):
    """Static project information plus the live environment health report."""

    def __init__(self, context: PageContext) -> None:
        super().__init__(context, "About", "Project information and environment status.")
        self._health_grid = QGridLayout()
        self._health_grid.setHorizontalSpacing(14)
        self._health_grid.setVerticalSpacing(10)
        self._health_grid.setColumnStretch(2, 1)

        content = QWidget()
        content.setObjectName("PageBody")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(16)
        column.addWidget(self._build_project_card())
        column.addWidget(self._build_servers_card())
        column.addWidget(self._build_health_card())
        column.addWidget(self._build_safety_card())
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.body.addWidget(scroll, 1)

        self.show_report(context.container.health_report)
        self.show_servers()

    def _build_project_card(self) -> Card:
        card = Card(APP_NAME)
        description = QLabel(APP_DESCRIPTION)
        description.setObjectName("Muted")
        description.setWordWrap(True)
        card.body.addWidget(description)

        paths = self.context.container.paths
        facts = QGridLayout()
        facts.setHorizontalSpacing(18)
        facts.setVerticalSpacing(6)
        facts.setColumnStretch(1, 1)
        rows = (
            ("Version", APP_VERSION),
            ("Project type", APP_ORGANIZATION),
            ("Data directory", str(paths.data_dir)),
            ("Database", paths.database_file.name),
        )
        for row, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setObjectName("Muted")
            text = QLabel(value)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setWordWrap(True)
            facts.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            facts.addWidget(text, row, 1)
        card.body.addLayout(facts)
        return card

    def _build_servers_card(self) -> Card:
        card = Card()
        title = QLabel("Local test servers")
        title.setObjectName("SectionTitle")
        hint = QLabel("Bound to this computer only (127.0.0.1).")
        hint.setObjectName("Muted")
        self.servers_button = QPushButton()
        self.servers_button.clicked.connect(self._toggle_servers)

        header = QHBoxLayout()
        header.addWidget(title)
        header.addSpacing(8)
        header.addWidget(hint)
        header.addStretch(1)
        header.addWidget(self.servers_button)
        card.body.addLayout(header)

        self._servers_grid = QGridLayout()
        self._servers_grid.setHorizontalSpacing(14)
        self._servers_grid.setVerticalSpacing(10)
        self._servers_grid.setColumnStretch(2, 1)
        card.body.addLayout(self._servers_grid)
        return card

    def _build_health_card(self) -> Card:
        card = Card()
        title = QLabel("Environment health")
        title.setObjectName("SectionTitle")
        self._summary = QLabel()
        self._summary.setObjectName("Muted")
        recheck = QPushButton("Run checks again")
        recheck.clicked.connect(self._rerun_checks)

        header = QHBoxLayout()
        header.addWidget(title)
        header.addSpacing(8)
        header.addWidget(self._summary)
        header.addStretch(1)
        header.addWidget(recheck)
        card.body.addLayout(header)
        card.body.addLayout(self._health_grid)
        return card

    @staticmethod
    def _build_safety_card() -> Card:
        card = Card("Safety scope")
        for point in _SAFETY_POINTS:
            label = QLabel(f"•  {point}")
            label.setObjectName("Muted")
            label.setWordWrap(True)
            card.body.addWidget(label)
        return card

    def show_report(self, report: HealthReport) -> None:
        """Render ``report`` into the health card, replacing earlier rows."""
        _clear_grid(self._health_grid)
        for row, result in enumerate(report.results):
            badge = StatusBadge(_LABEL_BY_STATUS[result.status], _TONE_BY_STATUS[result.status])
            badge.setMinimumWidth(64)
            detail = QLabel(result.detail)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            self._health_grid.addWidget(badge, row, 0)
            self._health_grid.addWidget(QLabel(result.name), row, 1)
            self._health_grid.addWidget(detail, row, 2)
        self._summary.setText(report.summary())

    def show_servers(self) -> None:
        """Render the current state of the bundled sample website."""
        _clear_grid(self._servers_grid)
        servers = self.context.container.servers
        for row, status in enumerate(servers.statuses()):
            badge = StatusBadge(
                "Running" if status.running else "Stopped",
                BadgeTone.SUCCESS if status.running else BadgeTone.NEUTRAL,
            )
            badge.setMinimumWidth(70)
            link = QLabel(
                f'<a href="{status.url}/">{status.url}</a>' if status.running else status.url
            )
            link.setObjectName("Muted")
            link.setOpenExternalLinks(True)
            self._servers_grid.addWidget(badge, row, 0)
            self._servers_grid.addWidget(QLabel(status.name), row, 1)
            self._servers_grid.addWidget(link, row, 2)
        self.servers_button.setText("Stop servers" if servers.all_running else "Start servers")

    def on_activated(self) -> None:
        self.show_servers()

    def _toggle_servers(self) -> None:
        servers = self.context.container.servers
        if servers.all_running:
            servers.stop_all()
        else:
            for message in servers.start_all():
                self.context.notifier.error(message)
        self.show_servers()
        self._rerun_checks()

    def _rerun_checks(self) -> None:
        container = self.context.container
        container.health_report = container.health_service.run()
        self.show_report(container.health_report)


def _clear_grid(grid: QGridLayout) -> None:
    """Remove and delete every widget in ``grid``."""
    while grid.count():
        item = grid.takeAt(0)
        widget = item.widget() if item is not None else None
        if widget is not None:
            widget.deleteLater()
