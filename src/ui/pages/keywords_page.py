"""Keyword library page."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from src.database.models import Keyword
from src.ui.dialogs.keyword_dialogs import BulkKeywordDialog, KeywordDialog
from src.ui.formatting import format_datetime
from src.ui.pages.base_page import PageContext
from src.ui.pages.crud_page import CrudPage
from src.ui.widgets.data_table import Column
from src.ui.widgets.form_dialog import Submit
from src.ui.widgets.status_badge import BadgeTone

IMPORT_FILTER = "Keyword lists (*.txt *.csv)"
EXPORT_FILTER = "CSV files (*.csv)"


class KeywordsPage(CrudPage[Keyword]):
    entity_name = "keyword"
    entity_plural = "keywords"
    empty_icon = "keywords"
    empty_description = "Keywords are reusable search terms you can pick when creating scenarios."
    supports_toggle = True

    def __init__(self, context: PageContext) -> None:
        super().__init__(context, "Keywords", "Reusable search keywords with tags and notes.")
        self._service = context.container.keywords
        self.setup(
            [
                Column("Keyword", lambda k: k.text, stretch=True),
                Column("Tags", lambda k: ", ".join(k.tags), width=220),
                Column("Notes", lambda k: k.notes.replace("\n", " "), width=240),
                Column(
                    "Status",
                    lambda k: "Enabled" if k.enabled else "Disabled",
                    badge=lambda k: BadgeTone.SUCCESS if k.enabled else BadgeTone.NEUTRAL,
                    width=100,
                ),
                Column("Updated", lambda k: format_datetime(k.updated_at), width=140),
            ],
            "Search keywords and notes…",
        )
        self.status_filter = self.add_filter(
            [("All statuses", None), ("Enabled", True), ("Disabled", False)]
        )
        self.tag_filter = self.add_filter([("All tags", None)])
        self.import_button = self.add_header_button("Import…", self._import)
        self.export_button = self.add_header_button("Export CSV", self._export)
        self.bulk_button = self.add_header_button("Bulk add", self._bulk_add)

    def on_activated(self) -> None:
        self._reload_tag_filter()
        super().on_activated()

    def _reload_tag_filter(self) -> None:
        current = self.tag_filter.currentData()
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("All tags", None)
        for tag in self._service.all_tags():
            self.tag_filter.addItem(tag, tag)
        self.tag_filter.setCurrentIndex(max(self.tag_filter.findData(current), 0))
        self.tag_filter.blockSignals(False)

    # -- hooks ------------------------------------------------------------------ #
    def fetch(self, text: str) -> Sequence[Keyword]:
        return self._service.search(
            text, enabled=self.status_filter.currentData(), tag=self.tag_filter.currentData()
        )

    def edit(self, entity: Keyword | None) -> bool:
        submit: Submit = (
            self._service.create if entity is None else partial(self._service.update, entity.id)
        )
        dialog = KeywordDialog(submit, entity, self.window())
        if not dialog.exec():
            return False
        self._reload_tag_filter()
        self.context.notifier.success("Keyword saved.")
        return True

    def describe(self, entity: Keyword) -> str:
        return entity.text

    def remove(self, entity: Keyword) -> None:
        self._service.delete(entity.id)

    def is_enabled(self, entity: Keyword) -> bool:
        return entity.enabled

    def set_enabled(self, entity: Keyword, enabled: bool) -> None:
        self._service.set_enabled(entity.id, enabled)

    # -- bulk / files --------------------------------------------------------------- #
    def _bulk_add(self) -> None:
        dialog = BulkKeywordDialog(self._service.bulk_add, self.window())
        if dialog.exec() and dialog.result_summary is not None:
            self.context.notifier.success(f"Bulk add: {dialog.result_summary.summary()}.")
            self._reload_tag_filter()
            self.refresh()

    def _import(self) -> None:
        start = str(self.context.container.paths.exports_dir)
        filename, _ = QFileDialog.getOpenFileName(self, "Import keywords", start, IMPORT_FILTER)
        if filename:
            self.import_from(Path(filename))

    def import_from(self, path: Path) -> None:
        """Import ``path`` and report the outcome (separate from the file dialog for tests)."""

        def action() -> None:
            result = self._service.import_file(path)
            self.context.notifier.success(f"Import: {result.summary()}.")

        if self.run_guarded(action):
            self._reload_tag_filter()
            self.refresh()

    def _export(self) -> None:
        start = str(self.context.container.paths.exports_dir / "keywords.csv")
        filename, _ = QFileDialog.getSaveFileName(self, "Export keywords", start, EXPORT_FILTER)
        if filename:
            self.export_to(Path(filename))

    def export_to(self, path: Path) -> None:
        def action() -> None:
            count = self._service.export_csv(path.with_suffix(".csv"))
            self.context.notifier.success(f"Exported {count} keywords to {path.name}.")

        self.run_guarded(action)
