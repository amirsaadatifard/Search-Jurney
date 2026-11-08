"""Dialog for creating/editing a target site."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QLineEdit, QPlainTextEdit, QWidget

from src.database.models import TargetSite
from src.domain.enums import Environment
from src.ui.widgets.form_dialog import FormDialog, Submit, make_combo

ENVIRONMENT_LABELS: dict[Environment, str] = {
    Environment.LOCAL: "Local (this computer)",
    Environment.DEVELOPMENT: "Development",
    Environment.STAGING: "Staging",
    Environment.CONTROLLED_TEST: "Controlled test",
}


class TargetSiteDialog(FormDialog):
    def __init__(self, submit: Submit, site: TargetSite | None, parent: QWidget | None) -> None:
        super().__init__("Edit target site" if site else "New target site", submit, parent)
        self.name_input = QLineEdit(site.display_name if site else "")
        self.url_input = QLineEdit(site.base_url if site else "")
        self.url_input.setPlaceholderText("http://127.0.0.1:8766/")
        self.environment_input = make_combo(
            [(label, environment) for environment, label in ENVIRONMENT_LABELS.items()],
            site.environment if site else Environment.LOCAL,
        )
        self.notes_input = QPlainTextEdit(site.notes if site else "")
        self.notes_input.setFixedHeight(70)
        self.enabled_input = QCheckBox("Enabled")
        self.enabled_input.setChecked(site.enabled if site else True)
        self.approved_input = QCheckBox(
            "Approved for automation — I own this site or am authorised to test it"
        )
        self.approved_input.setChecked(site.approved_for_automation if site else False)

        self.add_field("display_name", "Display name", self.name_input)
        self.add_field(
            "base_url",
            "Base URL",
            self.url_input,
            "http or https only. Public search engines cannot be added.",
        )
        self.add_field(
            "environment",
            "Environment",
            self.environment_input,
            "Local sites must use localhost or a loopback address.",
        )
        self.add_field("notes", "Notes", self.notes_input)
        self.add_checkbox("enabled", self.enabled_input)
        self.add_checkbox("approved_for_automation", self.approved_input)
        self.finish()

    def values(self) -> dict[str, Any]:
        return {
            "display_name": self.name_input.text(),
            "base_url": self.url_input.text(),
            "environment": self.environment_input.currentData(),
            "notes": self.notes_input.toPlainText(),
            "enabled": self.enabled_input.isChecked(),
            "approved_for_automation": self.approved_input.isChecked(),
        }
