"""Centralised filesystem locations.

Every module receives an :class:`AppPaths` instance instead of building paths
itself, which keeps tests hermetic (they pass a temporary directory).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.app.config import AppConfig
from src.app.constants import DATABASE_FILENAME, LOG_FILENAME


def absolute(path: Path) -> Path:
    """Make ``path`` absolute and normalised *without* following links.

    ``Path.resolve()`` would expand junctions and redirected folders, which can
    lengthen a path beyond the 260-character limit of default Windows setups.
    """
    return Path(os.path.abspath(path))


PROJECT_ROOT: Path = absolute(Path(__file__)).parents[2]


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All directories and files the application reads from or writes to."""

    root: Path
    data_dir: Path
    database_override: Path | None = None
    logs_override: Path | None = None
    screenshots_override: Path | None = None

    @classmethod
    def from_config(cls, config: AppConfig, root: Path = PROJECT_ROOT) -> AppPaths:
        """Resolve every location of ``config`` (relative paths are anchored at ``root``)."""

        def resolve(path: Path | None) -> Path | None:
            if path is None:
                return None
            return absolute(path if path.is_absolute() else root / path)

        base = cls.from_data_dir(config.data_dir, root)
        return cls(
            root=root,
            data_dir=base.data_dir,
            database_override=resolve(config.database_file),
            logs_override=resolve(config.logs_dir),
            screenshots_override=resolve(config.screenshots_dir),
        )

    @classmethod
    def from_data_dir(cls, data_dir: Path, root: Path = PROJECT_ROOT) -> AppPaths:
        """Resolve ``data_dir`` (relative paths are anchored at ``root``)."""
        resolved = data_dir if data_dir.is_absolute() else root / data_dir
        return cls(root=root, data_dir=absolute(resolved))

    @property
    def logs_dir(self) -> Path:
        return self.logs_override or self.data_dir / "logs"

    @property
    def screenshots_dir(self) -> Path:
        return self.screenshots_override or self.data_dir / "screenshots"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def database_file(self) -> Path:
        return self.database_override or self.data_dir / DATABASE_FILENAME

    @property
    def log_file(self) -> Path:
        return self.logs_dir / LOG_FILENAME

    @property
    def migrations_dir(self) -> Path:
        return self.root / "migrations"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def directories(self) -> tuple[Path, ...]:
        """Directories that must exist before the application starts."""
        return (
            self.data_dir,
            self.logs_dir,
            self.screenshots_dir,
            self.backups_dir,
            self.exports_dir,
            self.database_file.parent,
        )

    def ensure_directories(self) -> None:
        """Create every required directory (idempotent)."""
        for directory in self.directories:
            directory.mkdir(parents=True, exist_ok=True)

    def screenshots_dir_for(self, day: date) -> Path:
        """Return the dated screenshot folder, e.g. ``data/screenshots/2026-01-31``."""
        return self.screenshots_dir / day.isoformat()
