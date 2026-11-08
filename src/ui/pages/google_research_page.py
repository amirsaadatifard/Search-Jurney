"""Google Research: real search data -> target detection -> approved direct visit.

Flow shown on this page::

    keyword + target domain -> official Google search API -> real results table
        -> TARGET FOUND / NOT FOUND + observed position + target URL
        -> (approve the domain) -> Chromium opens the target URL directly
        -> dwell, safe scrolling, screenshots -> session report

Nothing is clicked on Google and nothing here is hard-coded: if Google cannot
answer, the error is shown instead of any substitute data.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.app.health import HealthCheckResult, HealthStatus
from src.database.models import ObservedResult, SearchObservation
from src.domain.exceptions import AppError, InvalidConfigurationError
from src.search_providers import ProviderCheck
from src.ui.dialogs.session_dialog import SessionDetailDialog
from src.ui.formatting import PLACEHOLDER
from src.ui.navigation import PageKey
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.widgets.card import Card
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.form_dialog import make_combo
from src.ui.widgets.status_badge import BadgeTone, StatusBadge
from src.ui.workers import TaskRunner

LANGUAGES: Final = (
    ("Any language", ""),
    ("English (en)", "en"),
    ("Persian (fa)", "fa"),
    ("Arabic (ar)", "ar"),
    ("German (de)", "de"),
    ("French (fr)", "fr"),
    ("Spanish (es)", "es"),
    ("Turkish (tr)", "tr"),
    ("Russian (ru)", "ru"),
)
COUNTRIES: Final = (
    ("Any country", ""),
    ("United States (us)", "us"),
    ("United Kingdom (gb)", "gb"),
    ("Iran (ir)", "ir"),
    ("Germany (de)", "de"),
    ("France (fr)", "fr"),
    ("Spain (es)", "es"),
    ("Turkey (tr)", "tr"),
    ("United Arab Emirates (ae)", "ae"),
    ("Canada (ca)", "ca"),
)
STEPS: Final = (
    "1  Enter keyword and domain",
    "2  Run search",
    "3  Review the result",
    "4  Run site test",
    "5  Watch the session",
    "6  Open the report",
)
_CHECK_TONES: Final = {
    HealthStatus.OK: (BadgeTone.SUCCESS, "OK"),
    HealthStatus.WARNING: (BadgeTone.WARNING, "Check"),
    HealthStatus.ERROR: (BadgeTone.DANGER, "Problem"),
}
API_NOTES: Final = (
    "Search data comes from Google's official Custom Search JSON API, which needs an API key and a "
    "Programmable Search Engine id configured to search the whole web. It returns at most 10 "
    "results per request and 100 per query, has a daily quota, and its ranking can differ from "
    "what google.com shows a person. Google decides who may use it: check availability for "
    "your project."
)
SAFETY_NOTE: Final = (
    "Only the search data comes from Google. The browser never visits Google and never clicks a "
    "result; it opens the detected URL directly, once per click, and only on a site you approved "
    "(a site you own or are authorised to test)."
)


class GoogleResearchPage(BasePage):
    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Google Research",
            "Real Google-backed search data, then a controlled visit to your approved target site.",
        )
        self._google = context.container.google
        self._tasks = TaskRunner(self)
        self.observation: SearchObservation | None = None
        self._visit_pending = False
        self._busy = False

        self.presentation_box = QCheckBox("Presentation mode")
        self.presentation_box.setToolTip("Guided steps, and the report opens by itself at the end.")
        self.presentation_box.toggled.connect(self._presentation_toggled)
        self.header.add_action(self.presentation_box)

        content = QWidget()
        content.setObjectName("PageBody")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(16)
        self.stepper = self._build_stepper()
        column.addWidget(self.stepper)
        column.addWidget(self._build_search_card())
        column.addWidget(self._build_result_card())
        column.addWidget(self._build_table_card(), 1)
        self.setup_card = self._build_setup_card()
        column.addWidget(self.setup_card)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.body.addWidget(scroll, 1)

        context.runs.finished.connect(self._on_run_finished)
        self._presentation_toggled(False)
        self._refresh_credentials()
        self._update_buttons()

    # -- construction ---------- #
    def _build_stepper(self) -> QWidget:
        frame = QWidget()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.step_badges = []
        for text in STEPS:
            badge = StatusBadge(text, BadgeTone.NEUTRAL)
            self.step_badges.append(badge)
            row.addWidget(badge)
        row.addStretch(1)
        return frame

    def _build_search_card(self) -> Card:
        card = Card("Search")
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("e.g. handmade ceramic mugs")
        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText("e.g. example.com")
        self.language_input = make_combo(list(LANGUAGES))
        self.country_input = make_combo(list(COUNTRIES))
        self.count_input = QSpinBox()
        self.count_input.setRange(0, 1000)  # wider than valid: the service explains the limit
        self.count_input.setValue(10)
        self.field_errors: dict[str, QLabel] = {}

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        fields = (
            ("keyword", "Keyword", self.keyword_input, 0, 0, 2),
            ("target_domain", "Target domain", self.domain_input, 0, 2, 2),
            ("language", "Language", self.language_input, 2, 0, 1),
            ("country", "Country / Region", self.country_input, 2, 1, 1),
            ("count", "Number of results", self.count_input, 2, 2, 1),
        )
        for key, caption, widget, row, column_, span in fields:
            label = QLabel(caption)
            label.setObjectName("FieldLabel")
            error = QLabel()
            error.setObjectName("FieldError")
            error.hide()
            self.field_errors[key] = error
            grid.addWidget(label, row, column_, 1, span)
            grid.addWidget(widget, row + 1, column_, 1, span)
            grid.addWidget(error, row + 2, column_, 1, span)
        card.body.addLayout(grid)

        self.test_button = QPushButton("TEST GOOGLE CONNECTION")
        self.test_button.clicked.connect(self.test_connection)
        self.search_button = QPushButton("RUN SEARCH")
        self.search_button.setProperty("variant", "primary")
        self.search_button.clicked.connect(self.run_search)
        self.open_button = QPushButton("OPEN TARGET SITE")
        self.open_button.clicked.connect(lambda: self.start_visit(quick=True))
        self.visit_button = QPushButton("RUN SITE TEST")
        self.visit_button.setProperty("variant", "primary")
        self.visit_button.clicked.connect(lambda: self.start_visit(quick=False))
        self.browser_input = make_combo([])
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for button in (self.test_button, self.search_button, self.open_button, self.visit_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(QLabel("Browser profile"))
        buttons.addWidget(self.browser_input)
        card.body.addLayout(buttons)

        self.status_line = QLabel()
        self.status_line.setWordWrap(True)
        self.status_line.hide()
        card.body.addWidget(self.status_line)
        note = QLabel(SAFETY_NOTE)
        note.setObjectName("Faint")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _build_result_card(self) -> Card:
        card = Card()
        self.verdict = QLabel("No search yet")
        self.verdict.setObjectName("MetricValue")
        self.verdict_detail = QLabel("Enter a keyword and a target domain, then press RUN SEARCH.")
        self.verdict_detail.setObjectName("Muted")
        self.verdict_detail.setWordWrap(True)
        self.verdict_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.approval_badge = StatusBadge("", BadgeTone.NEUTRAL)
        self.approval_badge.hide()
        self.approve_button = QPushButton("Approve this domain…")
        self.approve_button.clicked.connect(self.approve_target)
        self.approve_button.hide()
        row = QHBoxLayout()
        row.addWidget(self.verdict)
        row.addWidget(self.approval_badge)
        row.addStretch(1)
        row.addWidget(self.approve_button)
        card.body.addLayout(row)
        card.body.addWidget(self.verdict_detail)
        return card

    def _build_table_card(self) -> Card:
        card = Card("Search results")
        self.table: DataTable[ObservedResult] = DataTable(
            self.context.theme,
            [
                Column("Position", lambda r: str(r.position), lambda r: r.position, width=76),
                Column("Title", lambda r: r.title, stretch=True),
                Column("Domain", lambda r: r.domain, width=170),
                Column("URL", lambda r: r.url, width=250),
                Column("Snippet", lambda r: r.snippet, width=260),
                Column(
                    "Target Match",
                    lambda r: "MATCH" if r.is_target else PLACEHOLDER,
                    badge=lambda r: BadgeTone.SUCCESS if r.is_target else None,
                    width=120,
                ),
            ],
            highlight=lambda r: r.is_target,
        )
        self.table.setMinimumHeight(260)
        self.export_button = QPushButton("Export results (CSV)")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export_dialog)
        self.report_button = QPushButton("Open session report")
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.open_report)
        self.reports_page_button = QPushButton("Reports page")
        self.reports_page_button.clicked.connect(lambda: self.context.navigate(PageKey.REPORTS))
        footer = QHBoxLayout()
        self.table_note = QLabel()
        self.table_note.setObjectName("TableFooter")
        footer.addWidget(self.table_note, 1)
        for button in (self.export_button, self.report_button, self.reports_page_button):
            footer.addWidget(button)
        card.body.addWidget(self.table)
        card.body.addLayout(footer)
        return card

    def _build_setup_card(self) -> Card:
        card = Card("Google credentials and system checks")
        notes = QLabel(API_NOTES)
        notes.setObjectName("Muted")
        notes.setWordWrap(True)
        card.body.addWidget(notes)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.engine_input = QLineEdit()
        self.engine_input.setPlaceholderText("Search engine id (cx)")
        self.credential_status = QLabel()
        self.credential_status.setObjectName("Muted")
        save = QPushButton("Save credentials")
        save.clicked.connect(self.save_credentials)
        remove = QPushButton("Remove stored key")
        remove.clicked.connect(self._remove_key)
        self.credential_errors = {"api_key": QLabel(), "engine_id": QLabel()}
        for label in self.credential_errors.values():
            label.setObjectName("FieldError")
            label.hide()
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.addWidget(QLabel("API key"), 0, 0)
        grid.addWidget(self.api_key_input, 0, 1)
        grid.addWidget(self.credential_errors["api_key"], 1, 1)
        grid.addWidget(QLabel("Search engine id"), 2, 0)
        grid.addWidget(self.engine_input, 2, 1)
        grid.addWidget(self.credential_errors["engine_id"], 3, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(self.credential_status, 1)
        row.addWidget(remove)
        row.addWidget(save)
        card.body.addLayout(row)
        stored = QLabel(
            "The key is kept in the operating system's credential store, never in the "
            "database or logs."
        )
        stored.setObjectName("Faint")
        stored.setWordWrap(True)
        card.body.addWidget(stored)

        self.checks_button = QPushButton("Run system checks")
        self.checks_button.clicked.connect(self.run_system_checks)
        self.checks_grid = QGridLayout()
        self.checks_grid.setHorizontalSpacing(12)
        self.checks_grid.setVerticalSpacing(6)
        self.checks_grid.setColumnStretch(2, 1)
        heading = QHBoxLayout()
        title = QLabel("System checks")
        title.setObjectName("SectionTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.checks_button)
        card.body.addSpacing(8)
        card.body.addLayout(heading)
        card.body.addLayout(self.checks_grid)
        return card

    # -- lifecycle ---------- #
    def on_activated(self) -> None:
        profiles = self.context.container.browser_profiles.list()
        current = self.browser_input.currentData()
        self.browser_input.blockSignals(True)
        self.browser_input.clear()
        for profile in profiles:
            self.browser_input.addItem(profile.name, profile.id)
        index = self.browser_input.findData(current)
        self.browser_input.setCurrentIndex(max(index, 0))
        self.browser_input.blockSignals(False)
        self._refresh_credentials()
        if self.observation is not None:
            self._show_observation(self.observation)  # approval may have changed elsewhere
        self._update_buttons()

    def _presentation_toggled(self, on: bool) -> None:
        self.stepper.setVisible(on)
        self.setup_card.setVisible(True)
        self._set_step(self._current_step())

    # -- steps ---------- #
    def _current_step(self) -> int:
        if self.context.runs.is_active and self._visit_pending:
            return 5
        if self.context.runs.last_session_id is not None and self.report_button.isEnabled():
            return 6
        if self.observation is not None:
            return 4 if self._visit_allowed() else 3
        return 2 if (self.keyword_input.text() and self.domain_input.text()) else 1

    def _set_step(self, step: int) -> None:
        for number, badge in enumerate(self.step_badges, start=1):
            tone = (
                BadgeTone.SUCCESS
                if number < step
                else BadgeTone.INFO if number == step else BadgeTone.NEUTRAL
            )
            badge.set_tone(tone)

    # -- credentials ---------- #
    def _refresh_credentials(self) -> None:
        status = self._google.credential_status()
        if not self.engine_input.text():
            self.engine_input.setText(status.engine_id)
        self.api_key_input.setPlaceholderText(
            f"(stored {status.api_key_source} key)" if status.api_key_set else "API key"
        )
        self.credential_status.setText(
            "Credentials are configured." if status.complete else "Credentials are incomplete."
        )

    def save_credentials(self) -> None:
        for label in self.credential_errors.values():
            label.hide()
        try:
            self._google.credentials.save(self.api_key_input.text(), self.engine_input.text())
        except InvalidConfigurationError as exc:
            for key, message in exc.field_errors.items():
                if key in self.credential_errors:
                    self.credential_errors[key].setText(message)
                    self.credential_errors[key].show()
            self.context.notifier.error(exc.message)
            return
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        self.api_key_input.clear()
        self._refresh_credentials()
        self.context.notifier.success("Google credentials saved.")

    def _remove_key(self) -> None:
        self._google.credentials.clear_key()
        self._refresh_credentials()
        self.context.notifier.success("The stored API key was removed.")

    # -- connection test and system checks ---------- #
    def test_connection(self) -> None:
        self._set_busy(True, "Testing the Google connection…")

        def done(result: object) -> None:
            assert isinstance(result, ProviderCheck)
            self._set_busy(False)
            if result.ok:
                self._show_status(
                    f"Google connection OK ({result.latency_ms} ms).", BadgeTone.SUCCESS
                )
                self.context.notifier.success("Google connection OK.")
            else:
                self._show_status(f"Google connection failed: {result.detail}", BadgeTone.DANGER)

        self._tasks.submit(self._google.test_connection, done, self._on_error)

    def run_system_checks(self) -> None:
        domain = self.domain_input.text()
        self.checks_button.setEnabled(False)

        def done(result: object) -> None:
            assert isinstance(result, list)
            self.checks_button.setEnabled(True)
            self._show_checks(result)

        def failed(message: str) -> None:
            self.checks_button.setEnabled(True)
            self.context.notifier.error(message)

        self._tasks.submit(lambda: self._google.system_checks(domain), done, failed)

    def _show_checks(self, checks: list[HealthCheckResult]) -> None:
        while self.checks_grid.count():
            item = self.checks_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for row, check in enumerate(checks):
            tone, label = _CHECK_TONES[check.status]
            badge = StatusBadge(label, tone)
            badge.setMinimumWidth(64)
            detail = QLabel(check.detail)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            self.checks_grid.addWidget(badge, row, 0)
            self.checks_grid.addWidget(QLabel(check.name), row, 1)
            self.checks_grid.addWidget(detail, row, 2)

    # -- search ---------- #
    def run_search(self) -> None:
        for label in self.field_errors.values():
            label.hide()
        self.status_line.hide()
        self._set_busy(True, "Searching Google…")
        arguments = (
            self.keyword_input.text(),
            self.domain_input.text(),
            str(self.language_input.currentData() or ""),
            str(self.country_input.currentData() or ""),
            self.count_input.value(),
        )
        self._tasks.submit(
            lambda: self._google.run_search(*arguments), self._on_observation, self._on_search_error
        )

    def _on_observation(self, result: object) -> None:
        assert isinstance(result, SearchObservation)
        self._set_busy(False)
        self.observation = result
        self.report_button.setEnabled(False)
        self._show_observation(result)
        self._show_status(
            f"Search finished: {result.result_count} real results from {result.provider}.",
            BadgeTone.SUCCESS,
        )
        self._set_step(self._current_step())

    def _on_search_error(self, message: str) -> None:
        """Validation messages go next to their fields; everything else is shown prominently."""
        self._set_busy(False)
        self.verdict.setText("Search failed")
        self.verdict_detail.setText(
            "No results were stored. Nothing is substituted when Google is unavailable."
        )
        self._show_status(message, BadgeTone.DANGER)

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self._show_status(message, BadgeTone.DANGER)
        self.context.notifier.error(message)

    def _show_observation(self, observation: SearchObservation) -> None:
        self.table.set_rows(list(observation.results))
        self.export_button.setEnabled(bool(observation.results))
        self.table_note.setText(
            f"{observation.result_count} results · {observation.provider} · "
            f"{observation.created_at:%Y-%m-%d %H:%M} UTC"
        )
        if observation.target_found:
            self.verdict.setText("TARGET FOUND")
            self.verdict_detail.setText(
                f"Observed position: {observation.target_position}\n"
                f"Target URL: {observation.target_url}"
            )
            allowed = self._visit_allowed()
            self.approval_badge.set_status(
                "Approved for automation" if allowed else "Not approved",
                BadgeTone.SUCCESS if allowed else BadgeTone.WARNING,
            )
            self.approval_badge.show()
            self.approve_button.setVisible(not allowed)
        else:
            self.verdict.setText("TARGET NOT FOUND")
            self.verdict_detail.setText(
                f"{observation.target_domain} is not among the {observation.result_count} results "
                f"for “{observation.query}”."
            )
            self.approval_badge.hide()
            self.approve_button.hide()
        self._update_buttons()

    def _visit_allowed(self) -> bool:
        obs = self.observation
        return bool(
            obs
            and obs.target_found
            and obs.target_url
            and self._google.is_visit_allowed(obs.target_url)
        )

    # -- approval ---------- #
    def approve_target(self) -> None:
        obs = self.observation
        if obs is None or not obs.target_found:
            return
        if not ConfirmDialog.ask(
            self,
            "Approve this site for automation?",
            f"Chromium will be allowed to open {obs.target_url} and stay on that domain.\n\n"
            "Only approve a website that you own or are explicitly authorised to test.",
            confirm_text="I own it / I am authorised",
        ):
            return
        try:
            self._google.approve_target(obs.id)
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        self.context.notifier.success("Target site approved.")
        self._show_observation(obs)
        self._set_step(self._current_step())

    # -- site test ---------- #
    def start_visit(self, quick: bool) -> None:
        """Open the observed target URL directly in Chromium (one session)."""
        obs = self.observation
        if obs is None:
            return
        try:
            plan = self._google.plan_for(obs.id, int(self.browser_input.currentData()), quick)
            self.context.runs.start_plans([plan])
        except (AppError, TypeError) as exc:
            self.context.notifier.error(
                exc.message if isinstance(exc, AppError) else "Choose a browser profile."
            )
            return
        self._visit_pending = True
        self.report_button.setEnabled(False)
        self._update_buttons()
        self._set_step(5)
        self.context.navigate(PageKey.LIVE_VIEW)

    def _on_run_finished(self, summary: object) -> None:
        if not self._visit_pending:
            return
        self._visit_pending = False
        session_id = self.context.runs.last_session_id
        self.report_button.setEnabled(session_id is not None)
        self._update_buttons()
        self._set_step(6)
        self._show_status(
            "The site test finished. Open the session report or the Reports page.",
            BadgeTone.SUCCESS,
        )
        if self.presentation_box.isChecked() and session_id is not None:
            self.context.navigate(PageKey.GOOGLE_RESEARCH)
            self.open_report()

    def open_report(self) -> None:
        session_id = self.context.runs.last_session_id
        if session_id is None:
            return
        try:
            record = self.context.container.sessions.get_details(session_id)
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        SessionDetailDialog(record, self.context.theme, self.window()).exec()

    # -- export ---------- #
    def _export_dialog(self) -> None:
        default = (
            self.context.container.paths.exports_dir
            / f"google_results_{datetime.now():%Y%m%d_%H%M%S}.csv"
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export results", str(default), "CSV files (*.csv)"
        )
        if filename:
            self.export_to(Path(filename))

    def export_to(self, path: Path) -> None:
        obs = self.observation
        if obs is None:
            return
        self._tasks.submit(
            lambda: self._google.export_observation_csv(obs.id, path),
            lambda result: self.context.notifier.success(f"Exported to {Path(str(result)).name}."),
            self.context.notifier.error,
        )

    # -- state ---------- #
    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        if busy:
            self._show_status(text, BadgeTone.INFO)
        self._update_buttons()

    def _show_status(self, text: str, tone: BadgeTone) -> None:
        self.status_line.setText(text)
        self.status_line.setObjectName("FormError" if tone is BadgeTone.DANGER else "Muted")
        self.status_line.style().unpolish(self.status_line)
        self.status_line.style().polish(self.status_line)
        self.status_line.show()

    def _update_buttons(self) -> None:
        running = self.context.runs.is_active
        self.search_button.setEnabled(not self._busy and not running)
        self.test_button.setEnabled(not self._busy)
        ready = self._visit_allowed() and not self._busy and not running
        self.open_button.setEnabled(ready)
        self.visit_button.setEnabled(ready)
