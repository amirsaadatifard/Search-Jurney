"""Base class for data-entry dialogs with inline, per-field validation errors."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.exceptions import AppError, InvalidConfigurationError
from src.logging_config import get_logger

log = get_logger(__name__)

Submit = Callable[[dict[str, Any]], object]
FieldSpec = tuple[str, str, QWidget]


class FormDialog(QDialog):
    """Dialog that collects values, submits them and shows validation errors.

    Subclasses add inputs with :meth:`add_field` / :meth:`add_row` and implement
    :meth:`values`. ``submit`` (usually a service method) either returns the
    saved object or raises :class:`InvalidConfigurationError`; in that case the
    dialog stays open and marks the offending fields.
    """

    def __init__(
        self, title: str, submit: Submit, parent: QWidget | None = None, width: int = 520
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._submit = submit
        self._inputs: dict[str, QWidget] = {}
        self._error_labels: dict[str, QLabel] = {}
        self.saved: object | None = None

        heading = QLabel(title)
        heading.setObjectName("DialogTitle")

        body = QWidget()
        self._form = QVBoxLayout(body)
        self._form.setContentsMargins(0, 0, 10, 0)
        self._form.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        body.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)

        self.general_error = QLabel()
        self.general_error.setObjectName("FormError")
        self.general_error.setWordWrap(True)
        self.general_error.hide()

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 14, 18)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.general_error)
        layout.addLayout(buttons)

    # -- building --------------------------------------------------------------- #
    def add_section(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setObjectName("FormSection")
        self._form.addWidget(label)

    def add_field(self, name: str, label: str, widget: QWidget, hint: str = "") -> None:
        """Add a labelled input on its own row."""
        self._form.addWidget(self._field_block(name, label, widget, hint))

    def add_row(self, fields: Sequence[FieldSpec]) -> None:
        """Add several labelled inputs side by side."""
        row = QHBoxLayout()
        row.setSpacing(12)
        for name, label, widget in fields:
            row.addWidget(self._field_block(name, label, widget, ""), 1, Qt.AlignmentFlag.AlignTop)
        self._form.addLayout(row)

    def add_checkbox(self, name: str, checkbox: QCheckBox) -> None:
        self._inputs[name] = checkbox
        self._form.addWidget(checkbox)

    def finish(self) -> None:
        """Call after the last field so inputs stay grouped at the top."""
        self._form.addStretch(1)

    def _field_block(self, name: str, label: str, widget: QWidget, hint: str) -> QWidget:
        block = QWidget()
        column = QVBoxLayout(block)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        caption = QLabel(label)
        caption.setObjectName("FieldLabel")
        column.addWidget(caption)
        column.addWidget(widget)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("FieldHint")
            hint_label.setWordWrap(True)
            column.addWidget(hint_label)
        error = QLabel()
        error.setObjectName("FieldError")
        error.setWordWrap(True)
        error.hide()
        column.addWidget(error)
        self._inputs[name] = widget
        self._error_labels[name] = error
        return block

    # -- behaviour -------------------------------------------------------------- #
    def values(self) -> dict[str, Any]:
        """Return the current input as the dictionary passed to ``submit``."""
        raise NotImplementedError

    def save(self) -> None:
        """Submit the form; close on success, show errors otherwise."""
        self.set_errors({})
        try:
            self.saved = self._submit(self.values())
        except InvalidConfigurationError as exc:
            self.set_errors(exc.field_errors, fallback=exc.message)
            return
        except AppError as exc:
            self._show_general_error(exc.message)
            return
        except Exception:
            log.exception("form_submit_failed", dialog=self.windowTitle())
            self._show_general_error("An unexpected error occurred. See the log for details.")
            return
        self.accept()

    def set_errors(self, errors: Mapping[str, str], fallback: str = "") -> None:
        """Show ``errors`` next to their fields; unmatched ones go to the banner."""
        unmatched: list[str] = []
        for name, label in self._error_labels.items():
            message = errors.get(name, "")
            label.setText(message)
            label.setVisible(bool(message))
            self._mark_invalid(self._inputs[name], bool(message))
        for name, message in errors.items():
            if name not in self._error_labels:
                unmatched.append(message)
        if unmatched or (not errors and fallback):
            self._show_general_error(" ".join(unmatched) or fallback)
        else:
            self.general_error.hide()

    def error_for(self, name: str) -> str:
        """Currently displayed error for field ``name`` (empty if none)."""
        label = self._error_labels.get(name)
        return label.text() if label is not None else ""

    def _show_general_error(self, message: str) -> None:
        self.general_error.setText(message)
        self.general_error.show()

    @staticmethod
    def _mark_invalid(widget: QWidget, invalid: bool) -> None:
        widget.setProperty("invalid", "true" if invalid else "false")
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def make_combo(items: Sequence[tuple[str, Any]], current: Any = None) -> QComboBox:
    """Build a combo box from ``(label, data)`` pairs and select ``current``."""
    combo = QComboBox()
    for label, data in items:
        combo.addItem(label, data)
    select_combo_data(combo, current)
    return combo


def select_combo_data(combo: QComboBox, data: Any) -> None:
    """Select the entry whose user data equals ``data`` (no-op if absent)."""
    for position in range(combo.count()):
        if combo.itemData(position) == data:
            combo.setCurrentIndex(position)
            return
