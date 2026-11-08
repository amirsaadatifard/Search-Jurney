"""Create a desktop shortcut that starts the application without a console window.

Run it once with the project's Python interpreter::

    python scripts/create_shortcut.py

The shortcut launches ``pythonw.exe -m src.main`` (the windowless interpreter)
with the project folder as working directory. Windows only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.path.abspath(__file__)).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.app.constants import APP_SHORT_NAME  # noqa: E402

ICON_RELATIVE_PATH = Path("assets") / "app.ico"
ICON_SIZE = 256

_POWERSHELL_SCRIPT = """
$desktop = [Environment]::GetFolderPath('Desktop')
$path = Join-Path $desktop ($env:SJAT_SHORTCUT_NAME + '.lnk')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$shortcut.TargetPath = $env:SJAT_SHORTCUT_TARGET
$shortcut.Arguments = '-m src.main'
$shortcut.WorkingDirectory = $env:SJAT_SHORTCUT_WORKDIR
$shortcut.IconLocation = $env:SJAT_SHORTCUT_ICON
$shortcut.Description = $env:SJAT_SHORTCUT_NAME
$shortcut.Save()
Write-Output $path
"""


def windowless_interpreter() -> Path:
    """Return ``pythonw.exe`` next to the running interpreter."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if not candidate.is_file():
        raise FileNotFoundError(f"pythonw.exe not found next to {sys.executable}")
    return candidate


def write_icon(path: Path) -> None:
    """Render the application logo to an ``.ico`` file."""
    from PySide6.QtGui import QGuiApplication

    from src.ui.icons import make_app_image

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance() or QGuiApplication([])
    path.parent.mkdir(parents=True, exist_ok=True)
    if not make_app_image(ICON_SIZE).save(str(path), "ICO"):
        raise OSError(f"could not write {path}")
    del app


def create_shortcut(project_dir: Path) -> str:
    """Create the shortcut and return its location."""
    icon = project_dir / ICON_RELATIVE_PATH
    environment = os.environ | {
        "SJAT_SHORTCUT_NAME": APP_SHORT_NAME,
        "SJAT_SHORTCUT_TARGET": str(windowless_interpreter()),
        "SJAT_SHORTCUT_WORKDIR": str(project_dir),
        "SJAT_SHORTCUT_ICON": str(icon),
    }
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_SCRIPT],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=PROJECT_ROOT,
        help="folder the shortcut starts in (default: this project)",
    )
    parser.add_argument("--icon-only", action="store_true", help="only write assets/app.ico")
    args = parser.parse_args()

    write_icon(PROJECT_ROOT / ICON_RELATIVE_PATH)
    if args.icon_only:
        return 0
    if sys.platform != "win32":
        print("Desktop shortcuts are only supported on Windows.")
        return 1
    print(f"Shortcut created: {create_shortcut(args.project_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
