"""Approved target sites (the automation allowlist)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from src.database.models import TargetSite
from src.domain.enums import Environment
from src.ui.dialogs.target_site_dialog import ENVIRONMENT_LABELS, TargetSiteDialog
from src.ui.pages.base_page import PageContext
from src.ui.pages.crud_page import CrudPage
from src.ui.widgets.confirm_dialog import ConfirmDialog
from src.ui.widgets.data_table import Column
from src.ui.widgets.form_dialog import Submit
from src.ui.widgets.status_badge import BadgeTone

_SHORT_ENVIRONMENT: dict[Environment, str] = {
    Environment.LOCAL: "Local",
    Environment.DEVELOPMENT: "Development",
    Environment.STAGING: "Staging",
    Environment.CONTROLLED_TEST: "Controlled test",
}


class TargetsPage(CrudPage[TargetSite]):
    entity_name = "target site"
    entity_plural = "target sites"
    empty_icon = "targets"
    empty_description = (
        "Add the test websites you control. Automation refuses every address that is "
        "not listed here and approved."
    )
    supports_toggle = True

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Target Sites",
            "Test websites you control. Automation only runs against enabled, approved entries.",
        )
        self._service = context.container.target_sites
        self.setup(
            [
                Column("Name", lambda s: s.display_name, stretch=True),
                Column("Base URL", lambda s: s.base_url, width=260),
                Column("Domain", lambda s: s.domain, width=170),
                Column(
                    "Environment",
                    lambda s: _SHORT_ENVIRONMENT[s.environment],
                    badge=lambda s: (
                        BadgeTone.INFO if s.environment is Environment.LOCAL else BadgeTone.WARNING
                    ),
                    width=140,
                ),
                Column(
                    "Automation",
                    lambda s: "Approved" if s.approved_for_automation else "Not approved",
                    badge=lambda s: (
                        BadgeTone.SUCCESS if s.approved_for_automation else BadgeTone.DANGER
                    ),
                    width=130,
                ),
                Column(
                    "Status",
                    lambda s: "Enabled" if s.enabled else "Disabled",
                    badge=lambda s: BadgeTone.SUCCESS if s.enabled else BadgeTone.NEUTRAL,
                    width=100,
                ),
            ],
            "Search name, domain or URL…",
        )
        self.environment_filter = self.add_filter(
            [("All environments", None)]
            + [(label, environment) for environment, label in ENVIRONMENT_LABELS.items()]
        )
        self.approval_filter = self.add_filter(
            [("Any approval", None), ("Approved", True), ("Not approved", False)]
        )
        self.approve_button = self.add_row_button("Approve", self._toggle_approval)

    # -- hooks ------------------------------------------------------------------ #
    def fetch(self, text: str) -> Sequence[TargetSite]:
        return self._service.search(
            text,
            environment=self.environment_filter.currentData(),
            approved=self.approval_filter.currentData(),
        )

    def edit(self, entity: TargetSite | None) -> bool:
        submit: Submit = (
            self._service.create if entity is None else partial(self._service.update, entity.id)
        )
        dialog = TargetSiteDialog(submit, entity, self.window())
        if not dialog.exec():
            return False
        self.context.notifier.success("Target site saved.")
        return True

    def describe(self, entity: TargetSite) -> str:
        return entity.display_name

    def remove(self, entity: TargetSite) -> None:
        self._service.delete(entity.id)

    def is_enabled(self, entity: TargetSite) -> bool:
        return entity.enabled

    def set_enabled(self, entity: TargetSite, enabled: bool) -> None:
        self._service.set_enabled(entity.id, enabled)

    def on_selection(self, selected: list[TargetSite]) -> None:
        if len(selected) == 1:
            approved = selected[0].approved_for_automation
            self.approve_button.setText("Revoke approval" if approved else "Approve")

    # -- approval ------------------------------------------------------------------ #
    def _toggle_approval(self) -> None:
        selected = self.selected()
        if len(selected) != 1:
            return
        site = selected[0]
        approve = not site.approved_for_automation
        if approve and not ConfirmDialog.ask(
            self,
            "Approve for automation?",
            f"Automated browser sessions will be allowed to open {site.base_url}\n\n"
            "Only approve websites that you own or are explicitly authorised to test.",
            confirm_text="Approve",
        ):
            return
        message = (
            f"Approved '{site.display_name}'." if approve else f"Revoked '{site.display_name}'."
        )
        if self.run_guarded(lambda: self._service.set_approved(site.id, approve), message):
            self.refresh()
