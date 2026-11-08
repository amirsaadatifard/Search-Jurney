"""Theme system and main-window shell."""

from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from src.app.container import AppContainer
from src.services.settings_service import SettingKey
from src.ui.icons import icon_names, make_pixmap
from src.ui.main_window import PAGE_FACTORIES, MainWindow
from src.ui.navigation import PageKey, all_nav_items
from src.ui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    Palette,
    ThemeManager,
    ThemeMode,
    build_stylesheet,
)


@pytest.mark.parametrize("palette", [LIGHT_PALETTE, DARK_PALETTE])
def test_stylesheet_has_no_unresolved_tokens(palette: Palette) -> None:
    stylesheet = build_stylesheet(palette)
    assert "$" not in stylesheet
    assert palette.accent in stylesheet
    assert len(set(re.findall(r"#[0-9A-Fa-f]{6}", stylesheet))) > 10


def test_theme_mode_parse_falls_back_to_system() -> None:
    assert ThemeMode.parse("dark") is ThemeMode.DARK
    assert ThemeMode.parse("nonsense") is ThemeMode.SYSTEM
    assert ThemeMode.parse(None) is ThemeMode.SYSTEM


def test_every_navigation_item_has_a_page_and_icon() -> None:
    keys = [item.key for item in all_nav_items()]
    assert len(keys) == len(set(keys)) == len(PageKey)
    assert set(keys) == set(PAGE_FACTORIES)
    assert {item.icon for item in all_nav_items()} <= set(icon_names())


def test_icons_render_non_empty(qtbot: QtBot) -> None:
    for name in icon_names():
        image = make_pixmap(name, "#FF0000", 18).toImage()
        painted = any(
            image.pixelColor(x, y).alpha() > 0
            for x in range(image.width())
            for y in range(image.height())
        )
        assert painted, f"icon {name!r} rendered blank"


def test_main_window_navigation_and_theme_persistence(
    qtbot: QtBot, container: AppContainer
) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    theme = ThemeManager(app, ThemeMode.LIGHT)
    window = MainWindow(container, theme)
    qtbot.addWidget(window)

    assert window.current_page_key() is PageKey.DASHBOARD
    for key in PageKey:
        window._sidebar.page_requested.emit(key)
        assert window.current_page_key() is key

    received: list[Palette] = []
    theme.theme_changed.connect(received.append)
    window._sidebar.theme_mode_requested.emit(ThemeMode.DARK)

    assert theme.palette is DARK_PALETTE
    assert received == [DARK_PALETTE]
    assert container.settings.get(SettingKey.UI_THEME) == "dark"
    assert DARK_PALETTE.window in app.styleSheet()
