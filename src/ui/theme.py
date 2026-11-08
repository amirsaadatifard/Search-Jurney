"""Reusable theme system: colour palettes, stylesheet generation and switching.

Widgets never hard-code colours. They either rely on the generated stylesheet
(selected through ``objectName`` / dynamic properties) or read the active
:class:`Palette` from the :class:`ThemeManager` when they paint icons.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from string import Template

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from src.ui.icons import make_pixmap


class ThemeMode(StrEnum):
    """User-selectable theme preference."""

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"

    @classmethod
    def parse(cls, value: object) -> ThemeMode:
        """Convert a stored value to a mode, defaulting to ``SYSTEM``."""
        try:
            return cls(str(value))
        except ValueError:
            return cls.SYSTEM


@dataclass(frozen=True, slots=True)
class Palette:
    """Semantic colour tokens used by the stylesheet and by painted icons."""

    name: str
    window: str
    surface: str
    surface_alt: str
    sidebar: str
    sidebar_hover: str
    sidebar_active: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_soft: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    info: str
    info_bg: str
    neutral: str
    neutral_bg: str

    @property
    def is_dark(self) -> bool:
        return self.name == ThemeMode.DARK.value

    def tone(self, tone: str) -> tuple[str, str]:
        """Return ``(foreground, background)`` for a semantic tone name."""
        tones = {
            "success": (self.success, self.success_bg),
            "warning": (self.warning, self.warning_bg),
            "danger": (self.danger, self.danger_bg),
            "info": (self.info, self.info_bg),
        }
        return tones.get(tone, (self.neutral, self.neutral_bg))


LIGHT_PALETTE = Palette(
    name="light",
    window="#F4F5F7",
    surface="#FFFFFF",
    surface_alt="#F8F9FB",
    sidebar="#FFFFFF",
    sidebar_hover="#F1F3F6",
    sidebar_active="#E9EFFD",
    border="#E3E6EB",
    border_strong="#CDD2DA",
    text="#1B1F27",
    text_muted="#5E6673",
    text_faint="#8B93A1",
    accent="#2F62E0",
    accent_hover="#2754C6",
    accent_pressed="#1F46A8",
    accent_text="#FFFFFF",
    accent_soft="#E9EFFD",
    success="#17803D",
    success_bg="#E3F4E9",
    warning="#A15C07",
    warning_bg="#FCF0D9",
    danger="#C0362C",
    danger_bg="#FBE6E4",
    info="#1D5FAE",
    info_bg="#E2EEFB",
    neutral="#5E6673",
    neutral_bg="#ECEEF2",
)

DARK_PALETTE = Palette(
    name="dark",
    window="#13151A",
    surface="#1B1E25",
    surface_alt="#20242C",
    sidebar="#171A20",
    sidebar_hover="#20242C",
    sidebar_active="#232D45",
    border="#2A2F39",
    border_strong="#3A404D",
    text="#E7E9EE",
    text_muted="#A3AAB7",
    text_faint="#737B89",
    accent="#5B8AF5",
    accent_hover="#739BF7",
    accent_pressed="#4674DD",
    accent_text="#0E1220",
    accent_soft="#232D45",
    success="#4CC38A",
    success_bg="#17322A",
    warning="#E5A94B",
    warning_bg="#392C16",
    danger="#F0776C",
    danger_bg="#3D1F1D",
    info="#6FAEF5",
    info_bg="#1A2C42",
    neutral="#A3AAB7",
    neutral_bg="#272B34",
)

_STYLESHEET = Template("""
* { font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif; font-size: 10pt; }
QWidget { color: $text; }
QMainWindow, QWidget#AppRoot, QStackedWidget#PageStack, QWidget#PageBody { background: $window; }
QToolTip { background: $surface; color: $text; border: 1px solid $border_strong; padding: 4px 6px; }

/* Sidebar ---------------------------------------------------------------- */
QFrame#Sidebar { background: $sidebar; border: none; border-right: 1px solid $border; }
QLabel#SidebarTitle { font-size: 12pt; font-weight: 600; color: $text; }
QLabel#SidebarSubtitle { font-size: 8pt; color: $text_faint; }
QLabel#SidebarSection { font-size: 8pt; font-weight: 600; color: $text_faint; padding: 10px 14px 4px 14px; }
QPushButton#NavButton {
    text-align: left; padding: 8px 12px; margin: 1px 8px; border: none; border-radius: 6px;
    background: transparent; color: $text_muted;
}
QPushButton#NavButton:hover { background: $sidebar_hover; color: $text; }
QPushButton#NavButton:checked { background: $sidebar_active; color: $accent; font-weight: 600; }
QFrame#SidebarFooter { border-top: 1px solid $border; }
QPushButton#ThemeButton {
    border: 1px solid $border; background: transparent; padding: 5px; border-radius: 0px; min-width: 30px;
}
QPushButton#ThemeButton[position="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#ThemeButton[position="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#ThemeButton:hover { background: $sidebar_hover; }
QPushButton#ThemeButton:checked { background: $accent_soft; border-color: $accent; }

/* Page chrome ------------------------------------------------------------ */
QLabel#PageTitle { font-size: 17pt; font-weight: 600; color: $text; }
QLabel#PageSubtitle { color: $text_muted; }
QLabel#SectionTitle { font-size: 11pt; font-weight: 600; color: $text; }
QLabel#Muted { color: $text_muted; }
QLabel#Faint { color: $text_faint; font-size: 9pt; }
QFrame#Card { background: $surface; border: 1px solid $border; border-radius: 10px; }
QFrame#Divider { background: $border; max-height: 1px; min-height: 1px; border: none; }

/* Metric card ------------------------------------------------------------ */
QFrame#MetricCard { background: $surface; border: 1px solid $border; border-radius: 10px; }
QLabel#MetricLabel { color: $text_muted; font-size: 9pt; }
QLabel#MetricValue { font-size: 20pt; font-weight: 600; color: $text; }
QLabel#MetricHint { color: $text_faint; font-size: 8.5pt; }

/* Status badge ----------------------------------------------------------- */
QLabel#StatusBadge { border-radius: 8px; padding: 3px 9px; font-size: 8.5pt; font-weight: 600; min-height: 14px; }
QLabel#StatusBadge[tone="success"] { color: $success; background: $success_bg; }
QLabel#StatusBadge[tone="warning"] { color: $warning; background: $warning_bg; }
QLabel#StatusBadge[tone="danger"] { color: $danger; background: $danger_bg; }
QLabel#StatusBadge[tone="info"] { color: $info; background: $info_bg; }
QLabel#StatusBadge[tone="neutral"] { color: $neutral; background: $neutral_bg; }

/* Empty state ------------------------------------------------------------ */
QLabel#EmptyTitle { font-size: 12pt; font-weight: 600; color: $text; }
QLabel#EmptyDescription { color: $text_muted; }

/* Buttons ---------------------------------------------------------------- */
QPushButton {
    background: $surface; border: 1px solid $border_strong; border-radius: 6px; padding: 6px 14px; color: $text;
}
QPushButton:hover { background: $surface_alt; }
QPushButton:pressed { background: $neutral_bg; }
QPushButton:disabled { color: $text_faint; border-color: $border; background: $surface_alt; }
QPushButton[variant="primary"] { background: $accent; border-color: $accent; color: $accent_text; font-weight: 600; }
QPushButton[variant="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_pressed; }
QPushButton[variant="primary"]:disabled { background: $neutral_bg; border-color: $border; color: $text_faint; }
QPushButton[variant="danger"] { color: $danger; border-color: $danger; }
QPushButton[variant="danger"]:hover { background: $danger_bg; }
QPushButton[variant="ghost"] { border-color: transparent; background: transparent; color: $text_muted; }
QPushButton[variant="ghost"]:hover { background: $neutral_bg; color: $text; }

/* Inputs ----------------------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QDateTimeEdit {
    background: $surface; border: 1px solid $border_strong; border-radius: 6px; padding: 5px 8px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QDateEdit:focus, QDateTimeEdit:focus { border-color: $accent; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled {
    background: $surface_alt; color: $text_faint;
}
QComboBox::drop-down, QDateEdit::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $surface; border: 1px solid $border_strong; outline: none;
    selection-background-color: $accent_soft; selection-color: $text;
}
QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QLabel { background: transparent; }
QLabel#FieldError { color: $danger; font-size: 9pt; }

/* Tables ----------------------------------------------------------------- */
QTableView, QTableWidget, QTreeView, QListView {
    background: $surface; alternate-background-color: $surface_alt; border: 1px solid $border;
    border-radius: 8px; gridline-color: transparent; outline: none;
    selection-background-color: $accent_soft; selection-color: $text;
}
QTableView::item, QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid $border; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: $surface_alt; color: $text_muted; font-weight: 600; font-size: 9pt;
    padding: 8px 10px; border: none; border-bottom: 1px solid $border;
}
QTableCornerButton::section { background: $surface_alt; border: none; }

/* Misc ------------------------------------------------------------------- */
QProgressBar { background: $neutral_bg; border: none; border-radius: 4px; height: 8px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 3px; min-height: 28px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 3px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QStatusBar { background: $surface; border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QTabWidget::pane { border: 1px solid $border; border-radius: 8px; background: $surface; top: -1px; }
QTabBar::tab { padding: 7px 14px; color: $text_muted; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $accent; border-bottom-color: $accent; font-weight: 600; }
QDialog { background: $window; }
QMenu { background: $surface; border: 1px solid $border_strong; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: $accent_soft; }
QSplitter::handle { background: $border; }
QFrame#Toast { background: $surface; border: 1px solid $border_strong; border-radius: 8px; }
QLabel#ToastText { color: $text; }
QLabel#FieldLabel { color: $text; font-weight: 600; font-size: 9.5pt; }
QLabel#FieldHint { color: $text_faint; font-size: 8.5pt; }
QLabel#FormSection { color: $text_muted; font-size: 8.5pt; font-weight: 600; padding-top: 6px; }
QLabel#FormError { color: $danger; background: $danger_bg; border-radius: 6px; padding: 8px 10px; }
QLabel#DialogTitle { font-size: 13pt; font-weight: 600; }
QLabel#TableFooter { color: $text_faint; font-size: 9pt; }
QLineEdit[invalid="true"], QSpinBox[invalid="true"], QComboBox[invalid="true"], QPlainTextEdit[invalid="true"] { border-color: $danger; }
QTableView::item:selected { background: $accent_soft; color: $text; }
""")


# Stylesheets can only reference arrow glyphs as image files, so the themed
# chevrons are rendered once per palette into a private temporary folder.
_ARROW_STYLESHEET = Template("""
QComboBox::down-arrow, QDateEdit::down-arrow { image: url($down); width: 12px; height: 12px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    border: none; background: transparent; width: 20px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url($up); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url($down); width: 10px; height: 10px; }
""")
_ARROW_SIZE = 12


def build_stylesheet(palette: Palette) -> str:
    """Render the application stylesheet for ``palette``."""
    tokens = {key: value for key, value in asdict(palette).items() if key != "name"}
    return _STYLESHEET.substitute(tokens)


class _ArrowAssets:
    """Writes chevron images for a palette and returns the matching style rules."""

    def __init__(self) -> None:
        self._directory: Path | None = None

    def stylesheet(self, palette: Palette) -> str:
        if self._directory is None:
            self._directory = Path(tempfile.mkdtemp(prefix="sjat-theme-"))
            atexit.register(shutil.rmtree, self._directory, ignore_errors=True)
        paths: dict[str, str] = {}
        for direction in ("down", "up"):
            path = self._directory / f"{palette.name}-{direction}.png"
            if not path.exists():
                make_pixmap(f"chevron_{direction}", palette.text_muted, _ARROW_SIZE).save(str(path))
            paths[direction] = path.as_posix()
        return _ARROW_STYLESHEET.substitute(paths)


class ThemeManager(QObject):
    """Applies the selected theme to the application and announces changes."""

    theme_changed = Signal(object)  # emits the active Palette

    def __init__(self, app: QApplication, mode: ThemeMode = ThemeMode.SYSTEM) -> None:
        super().__init__(app)
        self._app = app
        self._mode = mode
        self._palette = self._resolve(mode)
        self._arrows = _ArrowAssets()
        app.setStyle("Fusion")
        app.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        self._apply()

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def palette(self) -> Palette:
        """The palette currently in effect (never ``SYSTEM``; already resolved)."""
        return self._palette

    def set_mode(self, mode: ThemeMode) -> None:
        """Switch theme mode and restyle the application."""
        self._mode = mode
        self._refresh()

    def _on_system_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._mode is ThemeMode.SYSTEM:
            self._refresh()

    def _refresh(self) -> None:
        palette = self._resolve(self._mode)
        if palette is self._palette:
            return
        self._palette = palette
        self._apply()
        self.theme_changed.emit(palette)

    def _resolve(self, mode: ThemeMode) -> Palette:
        if mode is ThemeMode.LIGHT:
            return LIGHT_PALETTE
        if mode is ThemeMode.DARK:
            return DARK_PALETTE
        system_is_dark = self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        return DARK_PALETTE if system_is_dark else LIGHT_PALETTE

    def _apply(self) -> None:
        self._app.setPalette(_to_qpalette(self._palette))
        self._app.setStyleSheet(
            build_stylesheet(self._palette) + self._arrows.stylesheet(self._palette)
        )


def _to_qpalette(palette: Palette) -> QPalette:
    """Mirror the tokens into a ``QPalette`` so native-drawn parts match."""
    qpalette = QPalette()
    roles = {
        QPalette.ColorRole.Window: palette.window,
        QPalette.ColorRole.WindowText: palette.text,
        QPalette.ColorRole.Base: palette.surface,
        QPalette.ColorRole.AlternateBase: palette.surface_alt,
        QPalette.ColorRole.Text: palette.text,
        QPalette.ColorRole.Button: palette.surface,
        QPalette.ColorRole.ButtonText: palette.text,
        QPalette.ColorRole.ToolTipBase: palette.surface,
        QPalette.ColorRole.ToolTipText: palette.text,
        QPalette.ColorRole.Highlight: palette.accent,
        QPalette.ColorRole.HighlightedText: palette.accent_text,
        QPalette.ColorRole.PlaceholderText: palette.text_faint,
        QPalette.ColorRole.Link: palette.accent,
    }
    for role, colour in roles.items():
        qpalette.setColor(role, QColor(colour))
    return qpalette
