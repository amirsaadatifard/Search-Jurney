"""Dialog for creating/editing a scenario.

The search source is not a choice any more — every scenario reads its ranking
from the real Google results, so the form configures the keyword, the approved
site to open and the behaviour on that page only.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from src.database.models import Scenario
from src.services.scenario_service import ScenarioFormOptions
from src.ui.widgets.form_dialog import FormDialog, Submit, make_combo


def _read_only(text: str) -> QLabel:
    """A fact the form reports but does not let the user change."""
    label = QLabel(text)
    label.setObjectName("FieldHint")
    return label


def _spin(minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


class ScenarioDialog(FormDialog):
    """Scenario editor.

    Spin-box ranges are intentionally wider than the valid ranges: the rules
    live in one place (the service layer), and the dialog shows their messages.
    """

    def __init__(
        self,
        submit: Submit,
        options: ScenarioFormOptions,
        scenario: Scenario | None,
        parent: QWidget | None,
    ) -> None:
        super().__init__("Edit scenario" if scenario else "New scenario", submit, parent, 600)
        self.resize(620, 760)
        s = scenario

        # General ------------------------------------------------------------------
        self.name_input = QLineEdit(s.name if s else "")
        self.description_input = QPlainTextEdit(s.description if s else "")
        self.description_input.setFixedHeight(56)
        self.keyword_input = QComboBox()
        self.keyword_input.setEditable(True)
        self.keyword_input.addItems(options.keywords)
        self.keyword_input.setCurrentText(s.keyword if s else "")
        keyword_edit = self.keyword_input.lineEdit()
        if keyword_edit is not None:
            keyword_edit.setPlaceholderText("Type or pick from the library")

        # Target and search ----------------------------------------------------------
        self.target_input = make_combo(
            [
                (
                    site.display_name + ("" if site.is_automation_allowed else "  (not approved)"),
                    site.id,
                )
                for site in options.target_sites
            ],
            s.target_site_id if s else None,
        )

        # Timing -----------------------------------------------------------------------
        self.min_dwell_input = _spin(
            0, 100000, s.min_dwell_seconds if s else options.default_min_dwell, " s"
        )
        self.max_dwell_input = _spin(
            0, 100000, s.max_dwell_seconds if s else options.default_max_dwell, " s"
        )
        self.iterations_input = _spin(0, 100000, s.iterations if s else 1)
        self.delay_input = _spin(0, 100000, s.delay_between_iterations if s else 2, " s")

        # Browser and session -------------------------------------------------------------
        self.browser_input = make_combo(
            [(profile.name, profile.id) for profile in options.browser_profiles],
            s.browser_profile_id if s else None,
        )
        self.proxy_input = make_combo(
            [("No proxy (direct connection)", None)]
            + [(profile.name, profile.id) for profile in options.proxy_profiles],
            s.proxy_profile_id if s else None,
        )
        self.proxy_input.setToolTip(
            "With a proxy the browser cannot be fenced at the network level: redirects outside "
            "the approved sites are detected and stop the session, but may reach the proxy first."
        )
        self.incognito_input = QCheckBox("Use a fresh, isolated browser context for every session")
        self.incognito_input.setChecked(s.incognito if s else True)
        self.clear_cookies_input = QCheckBox("Clear cookies between sessions")
        self.clear_cookies_input.setChecked(s.clear_cookies_between_sessions if s else True)
        self.clear_storage_input = QCheckBox("Clear local storage between sessions")
        self.clear_storage_input.setChecked(s.clear_storage_between_sessions if s else True)

        # Behaviour ---------------------------------------------------------------------------
        self.scroll_input = QCheckBox("Scroll the page while dwelling")
        self.scroll_input.setChecked(s.scroll_enabled if s else True)
        self.max_scroll_input = _spin(0, 1000, s.max_scroll_actions if s else 5)
        self.scroll_input.toggled.connect(self.max_scroll_input.setEnabled)
        self.max_scroll_input.setEnabled(self.scroll_input.isChecked())
        self.enabled_input = QCheckBox("Scenario enabled")
        self.enabled_input.setChecked(s.enabled if s else True)

        self._build()

    def _build(self) -> None:
        self.add_section("General")
        self.add_field("name", "Name", self.name_input)
        self.add_field("description", "Description", self.description_input)
        self.add_field("keyword", "Search keyword", self.keyword_input)

        self.add_section("Target and search")
        self.add_field(
            "target_site_id",
            "Target site",
            self.target_input,
            "Only enabled, approved sites can be used.",
        )
        self.add_field(
            "search_source",
            "Search source",
            _read_only("Google (official search API)"),
            "Before each iteration the real ranking of this site is read for the keyword; "
            "the iteration then opens the site directly. Nothing is typed into google.com.",
        )

        self.add_section("Timing")
        self.add_row(
            [
                ("min_dwell_seconds", "Minimum dwell time", self.min_dwell_input),
                ("max_dwell_seconds", "Maximum dwell time", self.max_dwell_input),
            ]
        )
        self.add_row(
            [
                ("iterations", "Iterations", self.iterations_input),
                ("delay_between_iterations", "Delay between iterations", self.delay_input),
            ]
        )

        self.add_section("Browser and session")
        self.add_row(
            [
                ("browser_profile_id", "Browser profile", self.browser_input),
                ("proxy_profile_id", "Proxy profile", self.proxy_input),
            ]
        )
        self.add_checkbox("incognito", self.incognito_input)
        self.add_checkbox("clear_cookies_between_sessions", self.clear_cookies_input)
        self.add_checkbox("clear_storage_between_sessions", self.clear_storage_input)

        self.add_section("Behaviour")
        self.add_checkbox("scroll_enabled", self.scroll_input)
        self.add_field("max_scroll_actions", "Maximum scroll actions", self.max_scroll_input)
        self.add_checkbox("enabled", self.enabled_input)
        self.finish()

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name_input.text(),
            "description": self.description_input.toPlainText(),
            "keyword": self.keyword_input.currentText(),
            "target_site_id": self.target_input.currentData(),
            "min_dwell_seconds": self.min_dwell_input.value(),
            "max_dwell_seconds": self.max_dwell_input.value(),
            "iterations": self.iterations_input.value(),
            "delay_between_iterations": self.delay_input.value(),
            "browser_profile_id": self.browser_input.currentData(),
            "proxy_profile_id": self.proxy_input.currentData(),
            "incognito": self.incognito_input.isChecked(),
            "clear_cookies_between_sessions": self.clear_cookies_input.isChecked(),
            "clear_storage_between_sessions": self.clear_storage_input.isChecked(),
            "scroll_enabled": self.scroll_input.isChecked(),
            "max_scroll_actions": self.max_scroll_input.value(),
            "enabled": self.enabled_input.isChecked(),
        }
