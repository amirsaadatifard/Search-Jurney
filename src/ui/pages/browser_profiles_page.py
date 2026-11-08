"""Browser profiles (viewport, locale, headless mode)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial

from src.database.models import BrowserProfile
from src.domain.enums import UserAgentMode
from src.ui.dialogs.browser_profile_dialog import BrowserProfileDialog
from src.ui.formatting import PLACEHOLDER, yes_no
from src.ui.pages.base_page import PageContext
from src.ui.pages.crud_page import CrudPage
from src.ui.widgets.data_table import Column
from src.ui.widgets.form_dialog import Submit
from src.ui.widgets.status_badge import BadgeTone


class BrowserProfilesPage(CrudPage[BrowserProfile]):
    entity_name = "browser profile"
    entity_plural = "browser profiles"
    empty_icon = "browser"
    empty_description = "A browser profile sets the window size, locale and session options."

    def __init__(self, context: PageContext) -> None:
        super().__init__(
            context,
            "Browser Profiles",
            "Viewport, locale, time zone and session options for the test browser.",
        )
        self._service = context.container.browser_profiles
        self.setup(
            [
                Column("Name", lambda p: p.name, stretch=True),
                Column(
                    "Viewport",
                    lambda p: f"{p.viewport_width} × {p.viewport_height}",
                    sort_key=lambda p: p.viewport_width * p.viewport_height,
                    width=130,
                ),
                Column(
                    "Mode",
                    lambda p: "Headless" if p.headless else "Visible",
                    badge=lambda p: BadgeTone.NEUTRAL if p.headless else BadgeTone.INFO,
                    width=110,
                ),
                Column("Locale", lambda p: p.locale or PLACEHOLDER, width=100),
                Column("Time zone", lambda p: p.timezone or PLACEHOLDER, width=160),
                Column("JavaScript", lambda p: yes_no(p.javascript_enabled), width=100),
                Column(
                    "Identification",
                    lambda p: (
                        "Default"
                        if p.user_agent_mode is UserAgentMode.BROWSER_DEFAULT
                        else "Test label"
                    ),
                    width=120,
                ),
            ],
            "Search profiles…",
        )

    def fetch(self, text: str) -> Sequence[BrowserProfile]:
        return self._service.list(text)

    def edit(self, entity: BrowserProfile | None) -> bool:
        submit: Submit = (
            self._service.create if entity is None else partial(self._service.update, entity.id)
        )
        dialog = BrowserProfileDialog(submit, entity, self.window())
        if not dialog.exec():
            return False
        self.context.notifier.success("Browser profile saved.")
        return True

    def describe(self, entity: BrowserProfile) -> str:
        return entity.name

    def remove(self, entity: BrowserProfile) -> None:
        self._service.delete(entity.id)
