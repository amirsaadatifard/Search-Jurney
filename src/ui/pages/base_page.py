"""Shared base classes for pages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.app.container import AppContainer
from src.ui.navigation import PageKey
from src.ui.run_controller import RunController
from src.ui.theme import ThemeManager, ThemeMode
from src.ui.widgets.card import Card
from src.ui.widgets.empty_state import EmptyState
from src.ui.widgets.page_header import PageHeader
from src.ui.widgets.toast import Notifier

PAGE_MARGIN = 28
PAGE_SPACING = 18


@dataclass(frozen=True, slots=True)
class PageContext:
    """Everything a page may depend on, passed in by the main window."""

    container: AppContainer
    theme: ThemeManager
    notifier: Notifier
    runs: RunController
    navigate: Callable[[PageKey], None]
    apply_theme: Callable[[ThemeMode], None]
    start_demo: Callable[[], None]


class BasePage(QWidget):
    """Common page scaffold: consistent margins, a header and a body layout."""

    def __init__(self, context: PageContext, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("PageBody")
        self.context = context
        self.header = PageHeader(title, subtitle)

        self.body = QVBoxLayout()
        self.body.setSpacing(PAGE_SPACING)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN - 4, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)
        layout.addWidget(self.header)
        layout.addLayout(self.body, 1)

    def on_activated(self) -> None:
        """Called each time the page becomes visible. Override to refresh data."""


class PlaceholderPage(BasePage):
    """A page whose functionality arrives in a later implementation phase."""

    def __init__(
        self, context: PageContext, title: str, subtitle: str, icon: str, planned: str
    ) -> None:
        super().__init__(context, title, subtitle)
        card = Card()
        card.body.addWidget(
            EmptyState(context.theme, icon, f"{title} is not available yet", planned), 1
        )
        self.body.addWidget(card, 1)
