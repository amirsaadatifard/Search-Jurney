"""Dialog for creating/editing a browser profile."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QLineEdit, QSpinBox, QWidget

from src.database.models import BrowserProfile
from src.domain.enums import UserAgentMode
from src.ui.widgets.form_dialog import FormDialog, Submit, make_combo

USER_AGENT_LABELS: dict[UserAgentMode, str] = {
    UserAgentMode.BROWSER_DEFAULT: "Browser default",
    UserAgentMode.LABELLED_TEST_AGENT: "Browser default + test-automation label",
}


def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


class BrowserProfileDialog(FormDialog):
    def __init__(
        self, submit: Submit, profile: BrowserProfile | None, parent: QWidget | None
    ) -> None:
        super().__init__(
            "Edit browser profile" if profile else "New browser profile", submit, parent
        )
        self.name_input = QLineEdit(profile.name if profile else "")
        # Ranges are wider than the valid ones so out-of-range input reaches validation.
        self.width_input = _spin(0, 10000, profile.viewport_width if profile else 1280, " px")
        self.height_input = _spin(0, 10000, profile.viewport_height if profile else 800, " px")
        self.locale_input = QLineEdit(profile.locale if profile else "")
        self.locale_input.setPlaceholderText("Browser default")
        self.timezone_input = QLineEdit(profile.timezone if profile else "")
        self.timezone_input.setPlaceholderText("Browser default")
        self.user_agent_input = make_combo(
            [(label, mode) for mode, label in USER_AGENT_LABELS.items()],
            profile.user_agent_mode if profile else UserAgentMode.BROWSER_DEFAULT,
        )
        self.headless_input = QCheckBox("Headless (run without a visible browser window)")
        self.headless_input.setChecked(profile.headless if profile else False)
        self.javascript_input = QCheckBox("JavaScript enabled")
        self.javascript_input.setChecked(profile.javascript_enabled if profile else True)
        self.downloads_input = QCheckBox("Allow downloads")
        self.downloads_input.setChecked(profile.downloads_enabled if profile else False)
        self.clear_storage_input = QCheckBox("Clear storage when a session ends")
        self.clear_storage_input.setChecked(profile.clear_storage if profile else True)

        self.add_field("name", "Name", self.name_input)
        self.add_row(
            [
                ("viewport_width", "Viewport width", self.width_input),
                ("viewport_height", "Viewport height", self.height_input),
            ]
        )
        self.add_row(
            [
                ("locale", "Locale (e.g. en-US)", self.locale_input),
                ("timezone", "Time zone (e.g. Europe/Berlin)", self.timezone_input),
            ]
        )
        self.add_field(
            "user_agent_mode",
            "Browser identification",
            self.user_agent_input,
            "The browser always identifies itself truthfully; impersonation is not supported.",
        )
        self.add_checkbox("headless", self.headless_input)
        self.add_checkbox("javascript_enabled", self.javascript_input)
        self.add_checkbox("downloads_enabled", self.downloads_input)
        self.add_checkbox("clear_storage", self.clear_storage_input)
        self.finish()

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name_input.text(),
            "viewport_width": self.width_input.value(),
            "viewport_height": self.height_input.value(),
            "locale": self.locale_input.text(),
            "timezone": self.timezone_input.text(),
            "user_agent_mode": self.user_agent_input.currentData(),
            "headless": self.headless_input.isChecked(),
            "javascript_enabled": self.javascript_input.isChecked(),
            "downloads_enabled": self.downloads_input.isChecked(),
            "clear_storage": self.clear_storage_input.isChecked(),
        }
