"""Shared scaffold for list-and-edit pages (scenarios, keywords, sites, profiles)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
)

from src.domain.exceptions import AppError
from src.logging_config import get_logger
from src.ui.pages.base_page import BasePage
from src.ui.widgets.card import Card
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.data_table import Column, DataTable
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.form_dialog import make_combo
from src.ui.widgets.search_box import SearchBox

log = get_logger(__name__)


class CrudPage[T](BasePage):
    """Search box, filters, sortable table and row actions around one entity type.

    Subclasses set the class attributes, call :meth:`setup` at the end of their
    ``__init__`` and implement the hook methods below.
    """

    entity_name: str = "item"
    entity_plural: str = "items"
    empty_icon: str = "inbox"
    empty_description: str = ""
    supports_duplicate: bool = False
    supports_toggle: bool = False

    # -- hooks ------------------------------------------------------------------ #
    def fetch(self, text: str) -> Sequence[T]:
        """Return the rows matching the search text and current filters."""
        raise NotImplementedError

    def edit(self, entity: T | None) -> bool:
        """Open the editor (``None`` = create). Return ``True`` if something was saved."""
        raise NotImplementedError

    def remove(self, entity: T) -> None:
        raise NotImplementedError

    def describe(self, entity: T) -> str:
        """Short human-readable name used in confirmations and notifications."""
        raise NotImplementedError

    def is_enabled(self, entity: T) -> bool:
        return True

    def set_enabled(self, entity: T, enabled: bool) -> None:
        raise NotImplementedError

    def duplicate(self, entity: T) -> None:
        raise NotImplementedError

    def on_selection(self, selected: list[T]) -> None:
        """Called after the selection changed (for page-specific buttons)."""

    # -- construction ------------------------------------------------------------- #
    def setup(self, columns: Sequence[Column[T]], search_placeholder: str) -> None:
        theme = self.context.theme
        self._single_buttons: list[QPushButton] = []
        self._filter_combos: list[QComboBox] = []

        self.new_button = QPushButton(f"New {self.entity_name}")
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(lambda: self._edit(None))
        self.header.add_action(self.new_button)

        self.search_box = SearchBox(theme, search_placeholder)
        self.search_box.search_changed.connect(lambda _text: self.refresh())
        self._filters = QHBoxLayout()
        self._filters.setSpacing(8)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)

        self.edit_button = self.add_row_button("Edit", self._edit_selected)
        if self.supports_duplicate:
            self.duplicate_button = self.add_row_button("Duplicate", self._duplicate_selected)
        if self.supports_toggle:
            self.toggle_button = self.add_row_button("Disable", self._toggle_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("variant", "danger")
        self.delete_button.clicked.connect(self._delete_selected)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self.search_box)
        toolbar.addLayout(self._filters)
        toolbar.addStretch(1)
        toolbar.addLayout(self._actions)
        toolbar.addWidget(self.delete_button)

        self.table: DataTable[T] = DataTable(theme, columns)
        self.table.object_activated.connect(self._edit)
        self.table.selection_changed.connect(self._update_actions)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        self.empty_state = EmptyState(
            theme,
            self.empty_icon,
            f"No {self.entity_plural} yet",
            self.empty_description,
            f"New {self.entity_name}",
        )
        if self.empty_state.action_button is not None:
            self.empty_state.action_button.clicked.connect(lambda: self._edit(None))
        empty_card = Card()
        empty_card.body.addWidget(self.empty_state, 1)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.table)
        self._stack.addWidget(empty_card)

        self.footer = QLabel()
        self.footer.setObjectName("TableFooter")

        content = QVBoxLayout()
        content.setSpacing(10)
        content.addLayout(toolbar)
        content.addWidget(self._stack, 1)
        content.addWidget(self.footer)
        self.body.addLayout(content, 1)
        self._update_actions()

    def add_filter(self, items: Sequence[tuple[str, Any]], tooltip: str = "") -> QComboBox:
        """Add a filter drop-down; changing it refreshes the table."""
        combo = make_combo(items)
        combo.setToolTip(tooltip)
        combo.setMinimumWidth(150)
        combo.currentIndexChanged.connect(lambda _index: self.refresh())
        self._filters.addWidget(combo)
        self._filter_combos.append(combo)
        return combo

    def add_row_button(self, text: str, handler: Callable[[], None]) -> QPushButton:
        """Add an action button that is enabled when exactly one row is selected."""
        button = QPushButton(text)
        button.clicked.connect(handler)
        self._actions.addWidget(button)
        self._single_buttons.append(button)
        return button

    def add_header_button(self, text: str, handler: Callable[[], None]) -> QPushButton:
        """Add a secondary button to the left of the primary "New" button."""
        button = QPushButton(text)
        button.clicked.connect(handler)
        self.header.insert_action_before_last(button)
        return button

    # -- data --------------------------------------------------------------------- #
    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Reload rows from the service using the current search text and filters."""
        text = self.search_box.text().strip()
        try:
            rows = list(self.fetch(text))
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return
        self.table.set_rows(rows)
        filtered = bool(text) or self.filters_active()
        if rows or filtered:
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentIndex(1)
        noun = self.entity_name if len(rows) == 1 else self.entity_plural
        suffix = " match the current filters" if filtered else ""
        self.footer.setText(f"{len(rows)} {noun}{suffix}")
        self._update_actions()

    def filters_active(self) -> bool:
        """Whether any filter drop-down deviates from its first ("all") entry."""
        return any(combo.currentIndex() > 0 for combo in self._filter_combos)

    def selected(self) -> list[T]:
        return self.table.selected_objects()

    def run_guarded(self, action: Callable[[], None], success: str = "") -> bool:
        """Run ``action``; report expected errors as a toast instead of raising."""
        try:
            action()
        except AppError as exc:
            self.context.notifier.error(exc.message)
            return False
        if success:
            self.context.notifier.success(success)
        return True

    # -- actions ------------------------------------------------------------------ #
    def _update_actions(self) -> None:
        selected = self.selected()
        single = len(selected) == 1
        for button in self._single_buttons:
            button.setEnabled(single)
        self.delete_button.setEnabled(bool(selected))
        if self.supports_toggle and single:
            self.toggle_button.setText("Disable" if self.is_enabled(selected[0]) else "Enable")
        self.on_selection(selected)

    def _edit(self, entity: T | None) -> None:
        if self.edit(entity):
            self.refresh()

    def _edit_selected(self) -> None:
        selected = self.selected()
        if len(selected) == 1:
            self._edit(selected[0])

    def _duplicate_selected(self) -> None:
        selected = self.selected()
        if len(selected) == 1 and self.run_guarded(
            lambda: self.duplicate(selected[0]), f"Duplicated '{self.describe(selected[0])}'."
        ):
            self.refresh()

    def _toggle_selected(self) -> None:
        selected = self.selected()
        if len(selected) != 1:
            return
        entity = selected[0]
        enable = not self.is_enabled(entity)
        verb = "Enabled" if enable else "Disabled"
        if self.run_guarded(
            lambda: self.set_enabled(entity, enable), f"{verb} '{self.describe(entity)}'."
        ):
            self.refresh()

    def _delete_selected(self) -> None:
        selected = self.selected()
        if not selected:
            return
        if len(selected) == 1:
            subject = f"'{self.describe(selected[0])}'"
        else:
            subject = f"{len(selected)} {self.entity_plural}"
        if not ConfirmDialog.ask(
            self,
            f"Delete {self.entity_name if len(selected) == 1 else self.entity_plural}?",
            f"{subject} will be permanently deleted. This cannot be undone.",
            confirm_text="Delete",
            danger=True,
        ):
            return
        deleted = 0
        for entity in selected:
            if self.run_guarded(lambda item=entity: self.remove(item)):  # type: ignore[misc]
                deleted += 1
        if deleted:
            noun = self.entity_name if deleted == 1 else self.entity_plural
            self.context.notifier.success(f"Deleted {deleted} {noun}.")
        self.refresh()

    def _show_context_menu(self, position: QPoint) -> None:
        if not self.selected():
            return
        menu = QMenu(self)
        for button in (*self._single_buttons, self.delete_button):
            action = menu.addAction(button.text())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(button.click)
        menu.exec(self.table.viewport().mapToGlobal(position))
