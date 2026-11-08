"""Application settings: appearance, storage, defaults, backup and configuration transfer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
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

from src.app.config import AppConfig
from src.domain.exceptions import AppError, InvalidConfigurationError
from src.domain.schemas import to_field_errors
from src.services.backup_service import BackupInfo
from src.services.config_transfer_service import ImportResult
from src.services.env_file import update_env_file
from src.services.settings_service import AutomationDefaults, SettingKey
from src.ui.navigation import PageKey
from src.ui.pages.base_page import BasePage, PageContext
from src.ui.theme import ThemeMode
from src.ui.widgets.card import Card
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.form_dialog import make_combo
from src.ui.workers import TaskRunner

THEME_CHOICES: Final = (
    ("Light", ThemeMode.LIGHT),
    ("Dark", ThemeMode.DARK),
    ("Follow the system", ThemeMode.SYSTEM),
)
SCREENSHOT_LABELS: Final = (
    ("search_results", "Search results page"),
    ("target_opened", "Target page after opening it"),
    ("session_end", "End of every session"),
    ("error", "When something fails"),
)
ENV_KEYS: Final = {
    "data_dir": "SJAT_DATA_DIR",
    "database_file": "SJAT_DATABASE_FILE",
    "screenshots_dir": "SJAT_SCREENSHOTS_DIR",
    "logs_dir": "SJAT_LOGS_DIR",
    "search_port": "SJAT_SEARCH_PORT",
    "target_port": "SJAT_TARGET_PORT",
}


def _size_text(size: int) -> str:
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


class SettingsPage(BasePage):
    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Settings",
            "Appearance, storage locations, automation defaults, backups and configuration.",
        )
        self._container = context.container
        self._tasks = TaskRunner(self)

        content = QWidget()
        content.setObjectName("PageBody")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(16)
        for card in (
            self._appearance_card(),
            self._defaults_card(),
            self._locations_card(),
            self._backup_card(),
            self._transfer_card(),
        ):
            column.addWidget(card)
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.body.addWidget(scroll, 1)
        context.theme.theme_changed.connect(lambda _palette: self._sync_theme_combo())

    def on_activated(self) -> None:
        self._load_defaults()
        self._sync_theme_combo()
        self.reload_backups()

    # -- appearance ------------------------------------------------------------------- #
    def _appearance_card(self) -> Card:
        card = Card("Appearance")
        self.theme_combo = make_combo(list(THEME_CHOICES), self.context.theme.mode)
        self.theme_combo.currentIndexChanged.connect(self._theme_chosen)
        row = QHBoxLayout()
        row.addWidget(QLabel("Theme"))
        row.addWidget(self.theme_combo)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _theme_chosen(self) -> None:
        mode = ThemeMode.parse(self.theme_combo.currentData())
        if mode is not self.context.theme.mode:
            self.context.apply_theme(mode)

    def _sync_theme_combo(self) -> None:
        self.theme_combo.blockSignals(True)
        index = self.theme_combo.findData(self.context.theme.mode)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self.theme_combo.blockSignals(False)

    # -- automation defaults ------------------------------------------------------------ #
    def _defaults_card(self) -> Card:
        card = Card("Automation defaults")
        card.body.addWidget(
            _muted("Used for new scenarios (dwell times) and for every run (timeout, screenshots).")
        )

        def spin(maximum: int, suffix: str = " s") -> QSpinBox:
            box = QSpinBox()
            box.setRange(0, maximum)
            box.setSuffix(suffix)
            return box

        self.min_dwell = spin(100000)
        self.max_dwell = spin(100000)
        self.timeout = spin(100000)
        self.concurrent = spin(1, "")
        self.concurrent.setValue(1)
        self.concurrent.setEnabled(False)
        self.concurrent.setToolTip("One session at a time. The design allows more later.")
        self.default_errors: dict[str, QLabel] = {}

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        rows = (
            ("min_dwell_seconds", "Default minimum dwell time", self.min_dwell),
            ("max_dwell_seconds", "Default maximum dwell time", self.max_dwell),
            ("timeout_seconds", "Default timeout per step", self.timeout),
            ("", "Maximum concurrent sessions", self.concurrent),
        )
        for index, (key, caption, widget) in enumerate(rows):
            grid.addWidget(QLabel(caption), index, 0)
            grid.addWidget(widget, index, 1)
            error = QLabel()
            error.setObjectName("FieldError")
            error.hide()
            grid.addWidget(error, index, 2)
            if key:
                self.default_errors[key] = error
        grid.setColumnStretch(2, 1)
        card.body.addLayout(grid)

        card.body.addWidget(QLabel("Screenshots"))
        self.shot_boxes = {key: QCheckBox(label) for key, label in SCREENSHOT_LABELS}
        for box in self.shot_boxes.values():
            card.body.addWidget(box)

        save = QPushButton("Save defaults")
        save.setProperty("variant", "primary")
        save.clicked.connect(self.save_defaults)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(save)
        card.body.addLayout(row)
        self._load_defaults()
        return card

    def _load_defaults(self) -> None:
        defaults = self._container.settings.automation_defaults()
        self.min_dwell.setValue(defaults.min_dwell_seconds)
        self.max_dwell.setValue(defaults.max_dwell_seconds)
        self.timeout.setValue(defaults.timeout_seconds)
        for key, box in self.shot_boxes.items():
            box.setChecked(defaults.screenshots.get(key, True))
        for label in self.default_errors.values():
            label.hide()

    def save_defaults(self) -> None:
        for label in self.default_errors.values():
            label.hide()
        defaults = AutomationDefaults(
            min_dwell_seconds=self.min_dwell.value(),
            max_dwell_seconds=self.max_dwell.value(),
            timeout_seconds=self.timeout.value(),
            screenshots={key: box.isChecked() for key, box in self.shot_boxes.items()},
        )
        try:
            self._container.settings.save_automation_defaults(defaults)
        except InvalidConfigurationError as exc:
            for key, message in exc.field_errors.items():
                if key in self.default_errors:
                    self.default_errors[key].setText(message)
                    self.default_errors[key].show()
            self.context.notifier.error(exc.message)
            return
        self.context.notifier.success("Defaults saved.")

    # -- locations and ports -------------------------------------------------------------- #
    def _locations_card(self) -> Card:
        card = Card("Storage locations and ports")
        card.body.addWidget(
            _muted(
                "Changes are written to the .env file and take effect after the application is "
                "restarted. Existing data is not moved: copy the database file yourself, or "
                "restore a backup into the new location."
            )
        )
        paths = self._container.paths
        config = self._container.config
        self.location_inputs: dict[str, QLineEdit] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        rows = (
            (
                "data_dir",
                "Data folder",
                str(config.data_dir) if config.data_dir.is_absolute() else str(paths.data_dir),
                True,
            ),
            ("database_file", "Database file", str(paths.database_file), False),
            ("screenshots_dir", "Screenshots folder", str(paths.screenshots_dir), True),
            ("logs_dir", "Logs folder", str(paths.logs_dir), True),
        )
        for index, (key, caption, value, is_folder) in enumerate(rows):
            field = QLineEdit(value)
            self.location_inputs[key] = field
            browse = QPushButton("Browse…")
            browse.clicked.connect(
                lambda _c=False, f=field, folder=is_folder: self._browse(f, folder)
            )
            opener = QPushButton("Open")
            opener.clicked.connect(lambda _c=False, f=field: self._open(f))
            grid.addWidget(QLabel(caption), index, 0)
            grid.addWidget(field, index, 1)
            grid.addWidget(browse, index, 2)
            grid.addWidget(opener, index, 3)
        card.body.addLayout(grid)

        self.search_port = QSpinBox()
        self.search_port.setRange(0, 99999)
        self.search_port.setValue(config.search_port)
        self.target_port = QSpinBox()
        self.target_port.setRange(0, 99999)
        self.target_port.setValue(config.target_port)
        ports = QHBoxLayout()
        ports.addWidget(QLabel("Search simulator port"))
        ports.addWidget(self.search_port)
        ports.addSpacing(20)
        ports.addWidget(QLabel("Test site port"))
        ports.addWidget(self.target_port)
        ports.addStretch(1)
        card.body.addLayout(ports)

        reset = QPushButton("Restore defaults")
        reset.clicked.connect(self._reset_locations)
        save = QPushButton("Save (restart required)")
        save.setProperty("variant", "primary")
        save.clicked.connect(self.save_locations)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(reset)
        row.addWidget(save)
        card.body.addLayout(row)
        return card

    def _browse(self, field: QLineEdit, folder: bool) -> None:
        start = field.text() or str(self._container.paths.data_dir)
        if folder:
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        else:
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Choose the database file", start, "SQLite database (*.db)"
            )
        if chosen:
            field.setText(chosen)

    @staticmethod
    def _open(field: QLineEdit) -> None:
        path = Path(field.text())
        target = path if path.is_dir() else path.parent
        if target.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def location_changes(self) -> dict[str, str | None]:
        """``.env`` changes needed to apply the values on screen (``None`` removes a key)."""
        paths, config = self._container.paths, self._container.config
        current = {
            "data_dir": str(paths.data_dir),
            "database_file": str(paths.database_file),
            "screenshots_dir": str(paths.screenshots_dir),
            "logs_dir": str(paths.logs_dir),
        }
        changes: dict[str, str | None] = {}
        for key, field in self.location_inputs.items():
            text = field.text().strip()
            if text and text != current[key]:
                changes[ENV_KEYS[key]] = text
        if self.search_port.value() != config.search_port:
            changes[ENV_KEYS["search_port"]] = str(self.search_port.value())
        if self.target_port.value() != config.target_port:
            changes[ENV_KEYS["target_port"]] = str(self.target_port.value())
        return changes

    def save_locations(self) -> None:
        changes = self.location_changes()
        if not changes:
            self.context.notifier.info("Nothing to change.")
            return
        try:
            candidate = {
                "data_dir": self.location_inputs["data_dir"].text().strip(),
                "search_port": self.search_port.value(),
                "target_port": self.target_port.value(),
            }
            AppConfig.model_validate(candidate)
            for key in ("database_file", "screenshots_dir", "logs_dir", "data_dir"):
                _check_location(self.location_inputs[key].text().strip(), key == "database_file")
            update_env_file(self._container.paths.env_file, changes)
        except ValidationError as exc:
            self.context.notifier.error(_describe(to_field_errors(exc)))
            return
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        self.context.notifier.success("Saved. Restart the application to use the new settings.")

    def _reset_locations(self) -> None:
        if not ConfirmDialog.ask(
            self,
            "Restore default locations?",
            "The location and port settings in the .env file are removed. The defaults apply "
            "after the next start.",
            confirm_text="Restore defaults",
        ):
            return
        try:
            update_env_file(self._container.paths.env_file, dict.fromkeys(ENV_KEYS.values()))
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        self.context.notifier.success("Defaults restored. Restart the application to apply them.")

    # -- backup and restore --------------------------------------------------------------- #
    def _backup_card(self) -> Card:
        card = Card("Backup and restore")
        card.body.addWidget(
            _muted(
                "A backup is a consistent copy of the whole database (scenarios, keywords, "
                "sessions, "
                "settings). Restoring replaces all current data; a safety copy is saved first."
            )
        )
        self.backup_table: DataTable[BackupInfo] = DataTable(
            self.context.theme,
            [
                Column("File", lambda b: b.name, stretch=True),
                Column(
                    "Created",
                    lambda b: f"{b.created:%Y-%m-%d %H:%M}",
                    lambda b: b.created,
                    width=150,
                ),
                Column(
                    "Size", lambda b: _size_text(b.size_bytes), lambda b: b.size_bytes, width=90
                ),
            ],
        )
        self.backup_table.setMinimumHeight(150)
        self.backup_table.selection_changed.connect(self._update_backup_buttons)
        self.backup_button = QPushButton("Back up now")
        self.backup_button.setProperty("variant", "primary")
        self.backup_button.clicked.connect(self.create_backup)
        self.restore_button = QPushButton("Restore selected…")
        self.restore_button.clicked.connect(self._restore_selected)
        self.restore_file_button = QPushButton("Restore from file…")
        self.restore_file_button.clicked.connect(self._restore_from_file)
        self.delete_backup_button = QPushButton("Delete")
        self.delete_backup_button.setProperty("variant", "danger")
        self.delete_backup_button.clicked.connect(self._delete_selected_backup)
        folder = QPushButton("Open folder")
        folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._container.paths.backups_dir))
            )
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        for button in (self.backup_button, self.restore_button, self.restore_file_button, folder):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.delete_backup_button)
        card.body.addWidget(self.backup_table)
        card.body.addLayout(row)
        self._update_backup_buttons()
        return card

    def reload_backups(self) -> None:
        self.backup_table.set_rows(self._container.backups.list_backups())
        self._update_backup_buttons()

    def _update_backup_buttons(self) -> None:
        one = len(self.backup_table.selected_objects()) == 1
        self.restore_button.setEnabled(one)
        self.delete_backup_button.setEnabled(one)

    def create_backup(self) -> None:
        self.backup_button.setEnabled(False)

        def done(result: object) -> None:
            assert isinstance(result, BackupInfo)
            self.backup_button.setEnabled(True)
            self.reload_backups()
            self.context.notifier.success(f"Backup saved: {result.name}")

        def failed(message: str) -> None:
            self.backup_button.setEnabled(True)
            self.context.notifier.error(message)

        self._tasks.submit(self._container.backups.create_backup, done, failed)

    def _restore_selected(self) -> None:
        selected = self.backup_table.selected_objects()
        if len(selected) == 1:
            self.restore_from(selected[0].path)

    def _restore_from_file(self) -> None:
        start = str(self._container.paths.backups_dir)
        filename, _ = QFileDialog.getOpenFileName(
            self, "Restore from backup file", start, "Database backups (*.db)"
        )
        if filename:
            self.restore_from(Path(filename))

    def restore_from(self, path: Path) -> bool:
        """Confirm, restore, refresh.

        Runs on the GUI thread so no page queries the database while it is replaced.
        """
        if not ConfirmDialog.ask(
            self,
            "Restore this backup?",
            "All current data (scenarios, keywords, sessions and settings) will be replaced by the "
            f"contents of '{path.name}'. A safety copy of the current data is saved first.\n\n"
            "This cannot be undone from within the application.",
            confirm_text="Replace all data",
            danger=True,
        ):
            return False
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            safety = self._container.backups.restore(path)
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return False
        finally:
            QApplication.restoreOverrideCursor()
        mode = ThemeMode.parse(self._container.settings.get(SettingKey.UI_THEME, ThemeMode.SYSTEM))
        self.context.apply_theme(mode)
        self.reload_backups()
        self.context.notifier.success(f"Backup restored. Previous data saved as {safety.name}.")
        self.context.navigate(PageKey.DASHBOARD)
        return True

    def _delete_selected_backup(self) -> None:
        selected = self.backup_table.selected_objects()
        if len(selected) != 1:
            return
        if not ConfirmDialog.ask(
            self,
            "Delete this backup?",
            f"'{selected[0].name}' will be permanently deleted.",
            "Delete",
            danger=True,
        ):
            return
        try:
            self._container.backups.delete_backup(selected[0].path)
        except AppError as exc:
            self.context.notifier.error(exc.message)
        self.reload_backups()

    # -- configuration transfer -------------------------------------------------------------- #
    def _transfer_card(self) -> Card:
        card = Card("Configuration export and import")
        card.body.addWidget(
            _muted(
                "Exports scenarios, keywords, target sites, browser and proxy profiles and your "
                "defaults as JSON. Passwords are never exported, and imported target sites must be "
                "approved again on this computer."
            )
        )
        self.overwrite_box = QCheckBox("Overwrite items that already exist (same name)")
        export = QPushButton("Export configuration…")
        export.clicked.connect(self._export_dialog)
        import_button = QPushButton("Import configuration…")
        import_button.clicked.connect(self._import_dialog)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(export)
        row.addWidget(import_button)
        row.addSpacing(12)
        row.addWidget(self.overwrite_box)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _export_dialog(self) -> None:
        default = (
            self._container.paths.exports_dir / f"configuration_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export configuration", str(default), "JSON files (*.json)"
        )
        if filename:
            self.export_to(Path(filename))

    def export_to(self, path: Path) -> None:
        self._tasks.submit(
            lambda: self._container.transfer.export_file(path),
            lambda result: self.context.notifier.success(
                f"Configuration exported to {Path(str(result)).name}."
            ),
            self.context.notifier.error,
        )

    def _import_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import configuration",
            str(self._container.paths.exports_dir),
            "JSON files (*.json)",
        )
        if filename:
            self.import_from(Path(filename))

    def import_from(self, path: Path) -> None:
        overwrite = self.overwrite_box.isChecked()

        def done(result: object) -> None:
            assert isinstance(result, ImportResult)
            text = result.summary() + "."
            if result.errors or result.notes:
                text += "\n" + "\n".join(
                    ["• " + line for line in [*result.errors[:8], *result.notes]]
                )
            (self.context.notifier.error if result.errors else self.context.notifier.success)(text)
            self.reload_backups()

        self._tasks.submit(
            lambda: self._container.transfer.import_file(path, overwrite),
            done,
            self.context.notifier.error,
        )


def _check_location(text: str, is_file: bool) -> None:
    """Reject locations that cannot work, with a message the user can act on."""
    if not text:
        return
    path = Path(text)
    if path.exists() and path.is_dir() == is_file:
        found, needed = ("folder", "file") if is_file else ("file", "folder")
        raise InvalidConfigurationError(f"'{text}' is a {found}, but a {needed} is needed.")
    anchor = path.parent if is_file else path
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    if not anchor.is_dir():
        raise InvalidConfigurationError(f"'{text}' is not on an available drive.")


def _describe(errors: dict[str, str]) -> str:
    """Readable text for validation errors, e.g. ``search port: Input should be ...``."""
    return "; ".join(
        message if field == "__all__" else f"{field.replace('_', ' ')}: {message}"
        for field, message in errors.items()
    )
