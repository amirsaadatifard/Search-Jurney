"""Application entry point: ``python -m src.main``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.app.bootstrap import bootstrap, shutdown
from src.app.constants import APP_NAME, APP_ORGANIZATION, APP_SLUG, APP_VERSION
from src.logging_config import get_logger
from src.services.settings_service import SettingKey
from src.ui.icons import make_app_icon
from src.ui.main_window import MainWindow
from src.ui.navigation import PageKey
from src.ui.theme import ThemeManager, ThemeMode

log = get_logger(__name__)

_SMOKE_TEST_DELAY_MS = 1200
_DEMO_START_DELAY_MS = 600


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.main", description=APP_NAME)
    parser.add_argument("--version", action="version", version=APP_VERSION)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="start the demo right away: run one safe search journey on the local test servers",
    )
    developer = parser.add_argument_group("developer options")
    developer.add_argument(
        "--smoke-test",
        action="store_true",
        help="start the application, then exit automatically (used by CI)",
    )
    developer.add_argument(
        "--screenshot", type=Path, help="with --smoke-test: save the window to this PNG file"
    )
    developer.add_argument(
        "--page", choices=[key.value for key in PageKey], help="page to open at startup"
    )
    developer.add_argument(
        "--theme", choices=[mode.value for mode in ThemeMode], help="override the saved theme"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the GUI and block until it closes. Returns the process exit code."""
    args = parse_args(sys.argv[1:] if argv is None else argv)

    _set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setWindowIcon(make_app_icon())
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORGANIZATION)

    container = bootstrap()
    try:
        saved_mode = ThemeMode.parse(container.settings.get(SettingKey.UI_THEME, ThemeMode.SYSTEM))
        theme = ThemeManager(app, ThemeMode(args.theme) if args.theme else saved_mode)

        server_errors = container.servers.start_all()
        # Re-run so the port checks reflect that the servers are now ours.
        container.health_report = container.health_service.run()

        window = MainWindow(container, theme)
        for message in server_errors:
            window.notifier.error(message)
        if args.page:
            window.show_page(PageKey(args.page))
        window.show()
        log.info("main_window_shown")
        if args.demo:
            QTimer.singleShot(_DEMO_START_DELAY_MS, window.start_demo)

        if args.smoke_test:
            QTimer.singleShot(
                _SMOKE_TEST_DELAY_MS, lambda: _finish_smoke_test(window, args.screenshot)
            )

        exit_code: int = app.exec()
    finally:
        shutdown(container)
    return exit_code


def _set_windows_app_id() -> None:
    """Give the process its own taskbar identity instead of the generic Python one."""
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_SLUG}.desktop")


def _finish_smoke_test(window: MainWindow, screenshot: Path | None) -> None:
    if screenshot is not None:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(screenshot))
    window.close()
    QApplication.quit()


if __name__ == "__main__":
    sys.exit(main())
