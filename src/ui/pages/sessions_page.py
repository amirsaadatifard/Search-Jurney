"""History of executed automation sessions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout

from src.database.models import AutomationSession
from src.domain.enums import SessionStatus
from src.domain.exceptions import AppError
from src.ui.dialogs.session_dialog import SessionDetailDialog
from src.ui.formatting import PLACEHOLDER, format_datetime, format_seconds, format_time, to_local
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.session_status import STATUS_LABELS, STATUS_TONES
from src.ui.widgets.card import Card
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.form_dialog import make_combo
from src.ui.widgets.search_box import SearchBox


def _finished_text(record: AutomationSession) -> str:
    """Time only when the session ended on the day it started (saves width)."""
    if record.finished_at is None:
        return PLACEHOLDER
    if to_local(record.finished_at).date() == to_local(record.started_at).date():
        return format_time(record.finished_at)
    return format_datetime(record.finished_at)


def _position(record: AutomationSession) -> str:
    if record.target_found is None:
        return PLACEHOLDER
    return f"#{record.target_position}" if record.target_position else "Not listed"


class SessionsPage(BasePage):
    """Every executed session; double-click a row for its full details."""

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Session History",
            "Every executed session with its timeline, settings snapshot and screenshots.",
        )
        theme = context.theme
        self._service = context.container.sessions

        self.search_box = SearchBox(theme, "Search scenario, keyword or target…")
        self.search_box.search_changed.connect(lambda _text: self.refresh())
        self.status_filter = make_combo(
            [("All statuses", None)] + [(STATUS_LABELS[status], status) for status in SessionStatus]
        )
        self.status_filter.currentIndexChanged.connect(lambda _index: self.refresh())
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button = QPushButton("Details")
        self.open_button.clicked.connect(self._open_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("variant", "danger")
        self.delete_button.clicked.connect(self._delete_selected)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self.search_box)
        toolbar.addWidget(self.status_filter)
        toolbar.addStretch(1)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.delete_button)

        self.table: DataTable[AutomationSession] = DataTable(
            theme,
            [
                Column("ID", lambda s: str(s.id), lambda s: s.id, width=52),
                Column("Scenario", lambda s: s.scenario_name, stretch=True),
                Column("Keyword", lambda s: s.keyword, stretch=True),
                Column("Target", lambda s: s.target_domain, width=105),
                Column(
                    "Started",
                    lambda s: format_datetime(s.started_at),
                    lambda s: s.started_at,
                    width=140,
                ),
                Column(
                    "Finished",
                    _finished_text,
                    lambda s: s.finished_at,
                    width=96,
                ),
                Column(
                    "Duration",
                    lambda s: format_seconds(s.duration_seconds),
                    lambda s: s.duration_seconds or 0,
                    width=82,
                ),
                Column("Position", _position, lambda s: s.target_position or 0, width=96),
                Column(
                    "Dwell",
                    lambda s: format_seconds(s.actual_dwell_seconds),
                    lambda s: s.actual_dwell_seconds or 0,
                    width=70,
                ),
                Column(
                    "Status",
                    lambda s: STATUS_LABELS[s.status],
                    badge=lambda s: STATUS_TONES[s.status],
                    width=100,
                ),
            ],
        )
        self.table.object_activated.connect(self.open_details)
        self.table.selection_changed.connect(self._update_buttons)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)

        self.empty_state = EmptyState(
            theme,
            "history",
            "No sessions yet",
            "Run a scenario from the Automation Runner and every session is recorded here.",
        )
        empty_card = Card()
        empty_card.body.addWidget(self.empty_state, 1)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.table)
        self._stack.addWidget(empty_card)
        self.footer = QLabel()
        self.footer.setObjectName("TableFooter")

        column = QVBoxLayout()
        column.setSpacing(10)
        column.addLayout(toolbar)
        column.addWidget(self._stack, 1)
        column.addWidget(self.footer)
        self.body.addLayout(column, 1)

        context.runs.finished.connect(lambda _summary: self.refresh())
        self._update_buttons()

    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        text = self.search_box.text().strip()
        data = self.status_filter.currentData()
        status = None if data is None else SessionStatus(str(data))
        try:
            rows = self._service.search(text, status)
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        self.table.set_rows(rows)
        filtered = bool(text) or status is not None
        self._stack.setCurrentIndex(0 if rows or filtered else 1)
        noun = "session" if len(rows) == 1 else "sessions"
        self.footer.setText(
            f"{len(rows)} {noun}" + (" match the current filters" if filtered else "")
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        selected = self.table.selected_objects()
        self.open_button.setEnabled(len(selected) == 1)
        self.delete_button.setEnabled(bool(selected))

    def open_details(self, record: AutomationSession) -> None:
        try:
            details = self._service.get_details(record.id)
        except AppError as exc:
            self.context.notifier.error(exc.message)
            self.refresh()
            return
        SessionDetailDialog(details, self.context.theme, self.window()).exec()

    def _open_selected(self) -> None:
        selected = self.table.selected_objects()
        if len(selected) == 1:
            self.open_details(selected[0])

    def _delete_selected(self) -> None:
        selected = self.table.selected_objects()
        if not selected:
            return
        noun = "session" if len(selected) == 1 else f"{len(selected)} sessions"
        if not ConfirmDialog.ask(
            self,
            "Delete sessions?",
            f"The {noun} and the screenshots saved for them will be permanently deleted.",
            confirm_text="Delete",
            danger=True,
        ):
            return
        try:
            removed = self._service.delete([s.id for s in selected])
        except AppError as exc:
            self.context.notifier.error(exc.message)
        else:
            self.context.notifier.success(
                f"Deleted {removed} {'session' if removed == 1 else 'sessions'}."
            )
        self.refresh()
