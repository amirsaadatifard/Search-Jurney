"""Sidebar navigation with a theme switcher in the footer."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.constants import APP_SHORT_NAME, APP_VERSION, SIDEBAR_WIDTH
from src.ui.icons import make_icon, make_pixmap
from src.ui.navigation import NAVIGATION, NavItem, PageKey
from src.ui.theme import Palette, ThemeManager, ThemeMode

_NAV_ICON_SIZE = 18
_THEME_BUTTONS: tuple[tuple[ThemeMode, str, str], ...] = (
    (ThemeMode.LIGHT, "sun", "Light theme"),
    (ThemeMode.DARK, "moon", "Dark theme"),
    (ThemeMode.SYSTEM, "system", "Follow the system theme"),
)


class Sidebar(QFrame):
    """Left-hand navigation. Emits :attr:`page_requested` with a :class:`PageKey`."""

    page_requested = Signal(str)
    theme_mode_requested = Signal(str)

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        self._theme = theme
        self._nav_buttons: dict[PageKey, tuple[QPushButton, NavItem]] = {}
        self._theme_buttons: dict[ThemeMode, tuple[QPushButton, str]] = {}
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self._logo = QLabel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_brand())
        layout.addWidget(self._build_navigation(), 1)
        layout.addWidget(self._build_footer())

        self._apply_palette(theme.palette)
        theme.theme_changed.connect(self._apply_palette)
        self.set_theme_mode(theme.mode)

    # -- public API --------------------------------------------------------- #
    def select(self, key: PageKey) -> None:
        """Mark ``key`` as the active page without emitting a request."""
        button, _ = self._nav_buttons[key]
        button.setChecked(True)

    def set_theme_mode(self, mode: ThemeMode) -> None:
        self._theme_buttons[mode][0].setChecked(True)

    # -- construction -------------------------------------------------------- #
    def _build_brand(self) -> QWidget:
        brand = QWidget()
        title = QLabel(APP_SHORT_NAME)
        title.setObjectName("SidebarTitle")
        subtitle = QLabel("Web Interaction Testing")
        subtitle.setObjectName("SidebarSubtitle")

        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(title)
        text.addWidget(subtitle)

        row = QHBoxLayout(brand)
        row.setContentsMargins(16, 18, 12, 12)
        row.setSpacing(10)
        row.addWidget(self._logo)
        row.addLayout(text, 1)
        return brand

    def _build_navigation(self) -> QWidget:
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 8)
        column.setSpacing(0)
        for section in NAVIGATION:
            heading = QLabel(section.title)
            heading.setObjectName("SidebarSection")
            column.addWidget(heading)
            for item in section.items:
                column.addWidget(self._build_nav_button(item))
        column.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(container)
        scroll.viewport().setAutoFillBackground(False)
        container.setAutoFillBackground(False)
        return scroll

    def _build_nav_button(self, item: NavItem) -> QPushButton:
        button = QPushButton(item.title)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(_NAV_ICON_SIZE, _NAV_ICON_SIZE))
        button.clicked.connect(lambda _checked=False, key=item.key: self.page_requested.emit(key))
        self._nav_group.addButton(button)
        self._nav_buttons[item.key] = (button, item)
        return button

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("Faint")

        switcher = QHBoxLayout()
        switcher.setSpacing(0)
        for index, (mode, icon, tooltip) in enumerate(_THEME_BUTTONS):
            button = QPushButton()
            button.setObjectName("ThemeButton")
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if index == 0:
                button.setProperty("position", "first")
            elif index == len(_THEME_BUTTONS) - 1:
                button.setProperty("position", "last")
            button.clicked.connect(
                lambda _checked=False, value=mode: self.theme_mode_requested.emit(value)
            )
            self._theme_group.addButton(button)
            self._theme_buttons[mode] = (button, icon)
            switcher.addWidget(button)

        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 10, 12, 10)
        row.addWidget(version)
        row.addStretch(1)
        row.addLayout(switcher)
        return footer

    # -- theming -------------------------------------------------------------- #
    def _apply_palette(self, palette: Palette) -> None:
        self._logo.setPixmap(make_pixmap("targets", palette.accent, 26))
        for button, item in self._nav_buttons.values():
            button.setIcon(make_icon(item.icon, palette.text_muted, palette.accent, _NAV_ICON_SIZE))
        for button, icon in self._theme_buttons.values():
            button.setIcon(make_icon(icon, palette.text_muted, palette.accent, 16))
