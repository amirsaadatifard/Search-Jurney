"""Dialogs for creating/editing a keyword and for adding many at once."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QCheckBox, QLineEdit, QPlainTextEdit, QWidget

from src.database.models import Keyword
from src.services.keyword_service import BulkAddResult
from src.ui.widgets.form_dialog import FormDialog, Submit

TAGS_HINT = "Comma-separated, e.g. demo, products"


def parse_tags(text: str) -> list[str]:
    """Split the comma-separated tag input into a list (validation happens later)."""
    return [part.strip() for part in text.split(",") if part.strip()]


class KeywordDialog(FormDialog):
    def __init__(self, submit: Submit, keyword: Keyword | None, parent: QWidget | None) -> None:
        super().__init__("Edit keyword" if keyword else "New keyword", submit, parent, width=460)
        self.text_input = QLineEdit(keyword.text if keyword else "")
        self.text_input.setPlaceholderText("e.g. handmade ceramic mugs")
        self.tags_input = QLineEdit(", ".join(keyword.tags) if keyword else "")
        self.notes_input = QPlainTextEdit(keyword.notes if keyword else "")
        self.notes_input.setFixedHeight(80)
        self.enabled_input = QCheckBox("Enabled")
        self.enabled_input.setChecked(keyword.enabled if keyword else True)

        self.add_field("text", "Keyword", self.text_input)
        self.add_field("tags", "Tags", self.tags_input, TAGS_HINT)
        self.add_field("notes", "Notes", self.notes_input)
        self.add_checkbox("enabled", self.enabled_input)
        self.finish()

    def values(self) -> dict[str, Any]:
        return {
            "text": self.text_input.text(),
            "tags": parse_tags(self.tags_input.text()),
            "notes": self.notes_input.toPlainText(),
            "enabled": self.enabled_input.isChecked(),
        }


class BulkKeywordDialog(FormDialog):
    """Paste one keyword per line; all receive the same optional tags."""

    def __init__(
        self,
        bulk_add: Callable[[list[str], list[str]], BulkAddResult],
        parent: QWidget | None,
    ) -> None:
        super().__init__(
            "Bulk add keywords",
            lambda values: bulk_add(values["lines"], values["tags"]),
            parent,
            width=480,
        )
        self.lines_input = QPlainTextEdit()
        self.lines_input.setPlaceholderText("One keyword per line")
        self.lines_input.setMinimumHeight(200)
        self.tags_input = QLineEdit()
        self.add_field("lines", "Keywords", self.lines_input, "Duplicates are skipped.")
        self.add_field("tags", "Tags for all keywords", self.tags_input, TAGS_HINT)
        self.save_button.setText("Add keywords")
        self.finish()

    def values(self) -> dict[str, Any]:
        return {
            "lines": self.lines_input.toPlainText().splitlines(),
            "tags": parse_tags(self.tags_input.text()),
        }

    @property
    def result_summary(self) -> BulkAddResult | None:
        return self.saved if isinstance(self.saved, BulkAddResult) else None
