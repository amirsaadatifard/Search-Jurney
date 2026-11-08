"""Details of one session: overview, timeline, settings snapshot, errors, screenshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.database.models import AutomationSession, Screenshot, SessionEvent
from src.domain.enums import EventType
from src.ui.formatting import PLACEHOLDER, format_datetime, format_seconds, format_time
from src.ui.session_status import STATUS_LABELS, STATUS_TONES
from src.ui.theme import ThemeManager
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.status_badge import StatusBadge

THUMBNAIL = QSize(220, 140)
ERROR_EVENTS = {EventType.ERROR, EventType.SAFETY_BLOCKED, EventType.SESSION_FAILED}


@dataclass(frozen=True, slots=True)
class Pair:
    """A key / value row."""

    key: str
    value: str


def flatten(data: dict[str, Any], prefix: str = "") -> list[Pair]:
    """``{"a": {"b": 1}}`` becomes ``[Pair("a.b", "1")]``; lists are shown as JSON."""
    pairs: list[Pair] = []
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            pairs.extend(flatten(value, f"{name}."))
        elif isinstance(value, list):
            pairs.append(Pair(name, json.dumps(value, ensure_ascii=False)))
        else:
            pairs.append(Pair(name, PLACEHOLDER if value is None else str(value)))
    return pairs


class ImageViewer(QDialog):
    """Shows one screenshot at full size inside a scroll area."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(path.name)
        self.resize(1000, 700)
        label = QLabel()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            label.setText("The image could not be loaded.")
        else:
            label.setPixmap(pixmap)
        scroll = QScrollArea()
        scroll.setWidget(label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)


class SessionDetailDialog(QDialog):
    def __init__(
        self, record: AutomationSession, theme: ThemeManager, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.record = record
        self._theme = theme
        self.setWindowTitle(f"Session #{record.id}")
        self.resize(940, 680)

        title = QLabel(f"Session #{record.id}")
        title.setObjectName("DialogTitle")
        badge = StatusBadge(STATUS_LABELS[record.status], STATUS_TONES[record.status])
        subtitle = QLabel(
            f"{record.scenario_name} · “{record.keyword}” · iteration "
            f"{record.iteration}/{record.total_iterations}"
        )
        subtitle.setObjectName("Muted")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(badge)
        header.addStretch(1)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._overview(), "Overview")
        self.tabs.addTab(self._timeline(), f"Timeline ({len(record.events)})")
        self.tabs.addTab(self._settings(), "Scenario settings")
        self.tabs.addTab(self._errors(), "Errors")
        self.tabs.addTab(self._screenshots(), f"Screenshots ({len(record.screenshots)})")

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(subtitle)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(footer)

    # -- tabs --------------------------------------------------------------------- #
    def _overview(self) -> QWidget:
        r = self.record
        position = (
            PLACEHOLDER
            if r.target_found is None
            else (f"#{r.target_position}" if r.target_position else "Not listed")
        )
        facts = [
            ("Scenario", r.scenario_name),
            ("Keyword", r.keyword),
            ("Target", f"{r.target_domain}  ({r.target_url})"),
            ("Iteration", f"{r.iteration} of {r.total_iterations}"),
            ("Started", format_datetime(r.started_at)),
            ("Finished", format_datetime(r.finished_at)),
            ("Duration", format_seconds(r.duration_seconds)),
            ("Status", STATUS_LABELS[r.status]),
            ("Final state", r.state.value),
            ("Target position", position),
            (
                "Results on page",
                str(r.results_count) if r.results_count is not None else PLACEHOLDER,
            ),
            ("Planned dwell", format_seconds(r.planned_dwell_seconds)),
            ("Actual dwell", format_seconds(r.actual_dwell_seconds)),
            ("Scroll actions", str(r.scroll_actions)),
            ("Browser profile", r.browser_profile_name or PLACEHOLDER),
            ("Proxy profile", r.proxy_profile_name or "Direct connection"),
        ]
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.addLayout(self._grid(facts))

        info = flatten(r.browser_info)
        if info:
            heading = QLabel("Browser")
            heading.setObjectName("SectionTitle")
            outer.addSpacing(10)
            outer.addWidget(heading)
            outer.addLayout(
                self._grid([(pair.key.replace("_", " ").capitalize(), pair.value) for pair in info])
            )
        outer.addStretch(1)
        return page

    @staticmethod
    def _grid(rows: list[tuple[str, str]]) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for index, (key, value) in enumerate(rows):
            caption = QLabel(key)
            caption.setObjectName("Muted")
            text = QLabel(value or PLACEHOLDER)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(caption, index, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(text, index, 1)
        return grid

    def _timeline(self) -> QWidget:
        self.timeline: DataTable[SessionEvent] = DataTable(
            self._theme,
            [
                Column("Time", lambda e: format_time(e.timestamp), lambda e: e.id, width=90),
                Column("Event", lambda e: e.event_type.value, width=190),
                Column("State", lambda e: e.state.value if e.state else PLACEHOLDER, width=170),
                Column("Details", lambda e: e.message, stretch=True),
            ],
        )
        self.timeline.keep_source_order()
        self.timeline.set_rows(list(self.record.events))
        return self.timeline

    def _settings(self) -> QWidget:
        self.settings_table: DataTable[Pair] = DataTable(
            self._theme,
            [
                Column("Setting", lambda p: p.key, width=260),
                Column("Value", lambda p: p.value, stretch=True),
            ],
        )
        self.settings_table.set_rows(flatten(self.record.settings_snapshot))
        return self.settings_table

    def _errors(self) -> QWidget:
        r = self.record
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        events = [e for e in r.events if e.event_type in ERROR_EVENTS]
        if not r.error_type and not events:
            note = QLabel("This session finished without errors.")
            note.setObjectName("Muted")
            layout.addWidget(note)
            layout.addStretch(1)
            return page
        if r.error_type:
            layout.addLayout(
                self._grid([("Error type", r.error_type), ("Message", r.error_message or "")])
            )
        self.error_table: DataTable[SessionEvent] = DataTable(
            self._theme,
            [
                Column("Time", lambda e: format_time(e.timestamp), width=90),
                Column("Event", lambda e: e.event_type.value, width=160),
                Column("Details", lambda e: e.message, stretch=True),
            ],
        )
        self.error_table.set_rows(events)
        layout.addSpacing(8)
        layout.addWidget(self.error_table, 1)
        return page

    def _screenshots(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        self.shot_list = QListWidget()
        self.shot_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.shot_list.setIconSize(THUMBNAIL)
        self.shot_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.shot_list.setSpacing(12)
        self.shot_list.setMovement(QListWidget.Movement.Static)
        for shot in self.record.screenshots:
            self.shot_list.addItem(self._shot_item(shot))
        self.shot_list.itemDoubleClicked.connect(self._open_shot)
        if not self.record.screenshots:
            note = QLabel("No screenshots were captured for this session.")
            note.setObjectName("Muted")
            layout.addWidget(note)
            layout.addStretch(1)
            return page
        hint = QLabel("Double-click a screenshot to view it full size.")
        hint.setObjectName("Faint")
        folder = QPushButton("Open folder")
        folder.clicked.connect(self._open_folder)
        row = QHBoxLayout()
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(folder)
        layout.addLayout(row)
        layout.addWidget(self.shot_list, 1)
        return page

    def _shot_item(self, shot: Screenshot) -> QListWidgetItem:
        path = Path(shot.file_path)
        item = QListWidgetItem(
            f"{shot.kind.value.replace('_', ' ').title()}\n{format_time(shot.captured_at)}"
        )
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            item.setText(item.text() + "\n(file missing)")
        else:
            item.setIcon(
                QIcon(
                    pixmap.scaled(
                        THUMBNAIL,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            )
        item.setToolTip(str(path))
        return item

    def _open_shot(self, item: QListWidgetItem) -> None:
        path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        if path.is_file():
            ImageViewer(path, self).exec()

    def _open_folder(self) -> None:
        if self.record.screenshots:
            folder = Path(self.record.screenshots[0].file_path).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
