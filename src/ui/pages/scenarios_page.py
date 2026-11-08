"""Scenario management page."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from src.database.models import Scenario
from src.ui.dialogs.scenario_dialog import ScenarioDialog
from src.ui.formatting import format_datetime, search_profile_label
from src.ui.pages.base_page import PageContext
from src.ui.pages.crud_page import CrudPage
from src.ui.widgets.data_table import Column
from src.ui.widgets.form_dialog import Submit
from src.ui.widgets.status_badge import BadgeTone


def _dwell_text(scenario: Scenario) -> str:
    return f"{scenario.min_dwell_seconds}–{scenario.max_dwell_seconds} s"


class ScenariosPage(CrudPage[Scenario]):
    entity_name = "scenario"
    entity_plural = "scenarios"
    empty_icon = "scenarios"
    empty_description = (
        "A scenario defines the keyword to look up on Google, the approved site to "
        "open and how long to stay there."
    )
    supports_duplicate = True
    supports_toggle = True

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Scenarios",
            "Read the real Google ranking for a keyword and visit the approved site from there.",
        )
        self._service = context.container.scenarios
        self.setup(
            [
                Column("Name", lambda s: s.name, stretch=True),
                Column("Keyword", lambda s: s.keyword, width=190),
                Column("Target site", lambda s: s.target_site.display_name, width=160),
                Column("Rank source", lambda s: search_profile_label(s.search_profile), width=150),
                Column("Dwell", _dwell_text, sort_key=lambda s: s.min_dwell_seconds, width=90),
                Column("Runs", lambda s: str(s.iterations), lambda s: s.iterations, width=70),
                Column(
                    "Status",
                    lambda s: "Enabled" if s.enabled else "Disabled",
                    badge=lambda s: BadgeTone.SUCCESS if s.enabled else BadgeTone.NEUTRAL,
                    width=100,
                ),
                Column("Updated", lambda s: format_datetime(s.updated_at), width=140),
            ],
            "Search scenarios…",
        )
        self.status_filter = self.add_filter(
            [("All statuses", None), ("Enabled", True), ("Disabled", False)]
        )
        self.target_filter = self.add_filter([("All target sites", None)])

    def on_activated(self) -> None:
        self._reload_target_filter()
        super().on_activated()

    def _reload_target_filter(self) -> None:
        current = self.target_filter.currentData()
        self.target_filter.blockSignals(True)
        self.target_filter.clear()
        self.target_filter.addItem("All target sites", None)
        for site in self._service.form_options().target_sites:
            self.target_filter.addItem(site.display_name, site.id)
        index = self.target_filter.findData(current)
        self.target_filter.setCurrentIndex(max(index, 0))
        self.target_filter.blockSignals(False)

    # -- hooks ------------------------------------------------------------------ #
    def fetch(self, text: str) -> Sequence[Scenario]:
        return self._service.search(
            text,
            enabled=self.status_filter.currentData(),
            target_site_id=self.target_filter.currentData(),
        )

    def edit(self, entity: Scenario | None) -> bool:
        options = self._service.form_options()
        submit: Submit = (
            self._service.create if entity is None else partial(self._service.update, entity.id)
        )
        dialog = ScenarioDialog(submit, options, entity, self.window())
        if not dialog.exec():
            return False
        self.context.notifier.success(f"Saved scenario '{self.describe(dialog.saved)}'.")
        return True

    def describe(self, entity: object) -> str:
        return str(getattr(entity, "name", ""))

    def remove(self, entity: Scenario) -> None:
        self._service.delete(entity.id)

    def is_enabled(self, entity: Scenario) -> bool:
        return entity.enabled

    def set_enabled(self, entity: Scenario, enabled: bool) -> None:
        self._service.set_enabled(entity.id, enabled)

    def duplicate(self, entity: Scenario) -> None:
        self._service.duplicate(entity.id)
