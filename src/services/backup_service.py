"""Database backup and restore.

* Backups use SQLite's online-backup API, so they are consistent even while the
  application is using the database.
* A file is only restorable if it is a healthy SQLite database of this
  application (integrity check, expected tables, known schema revision).
* Restoring replaces **all** current data, so the current database is backed up
  first (a "pre-restore" safety copy) and restore is refused while a run is active.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from alembic.config import Config
from alembic.script import ScriptDirectory

from src.app.paths import AppPaths
from src.database.migrator import DatabaseMigrator
from src.database.session import Database
from src.domain.exceptions import AppError, InvalidConfigurationError
from src.logging_config import get_logger

log = get_logger(__name__)

BACKUP_SUFFIX: Final = ".db"
REQUIRED_TABLES: Final = frozenset(
    {"alembic_version", "scenarios", "automation_sessions", "keywords"}
)
_LABEL_PATTERN: Final = re.compile(r"[^A-Za-z0-9_-]+")
SQLITE_MAGIC: Final = b"SQLite format 3\x00"


class BackupError(AppError):
    """A backup or restore could not be completed."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    path: Path
    created: datetime
    size_bytes: int

    @property
    def name(self) -> str:
        return self.path.name


class BackupService:
    def __init__(
        self,
        database: Database,
        paths: AppPaths,
        migrator: DatabaseMigrator,
        is_run_active: Callable[[], bool] = lambda: False,
    ) -> None:
        self._database = database
        self._paths = paths
        self._migrator = migrator
        self._is_run_active = is_run_active

    # -- backups ---------------------------------------------------------------- #
    def create_backup(self, label: str = "") -> BackupInfo:
        """Write a consistent copy of the database into the backups folder."""
        clean = _LABEL_PATTERN.sub("-", label).strip("-")[:40]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"sjat_{stamp}{'_' + clean if clean else ''}{BACKUP_SUFFIX}"
        target = self._paths.backups_dir / name
        counter = 1
        while target.exists():
            counter += 1
            target = self._paths.backups_dir / f"sjat_{stamp}_{counter}{BACKUP_SUFFIX}"
        self._paths.backups_dir.mkdir(parents=True, exist_ok=True)
        try:
            with (
                closing(sqlite3.connect(self._paths.database_file)) as source,
                closing(sqlite3.connect(target)) as destination,
            ):
                source.backup(destination)
        except (sqlite3.Error, OSError) as exc:
            target.unlink(missing_ok=True)
            raise BackupError(f"The backup could not be created: {exc}") from exc
        log.info("backup_created", file=target.name)
        return self._info(target)

    def list_backups(self) -> list[BackupInfo]:
        """Backups in the backups folder, newest first."""
        if not self._paths.backups_dir.is_dir():
            return []
        files = [p for p in self._paths.backups_dir.glob(f"*{BACKUP_SUFFIX}") if p.is_file()]
        return sorted((self._info(p) for p in files), key=lambda info: info.created, reverse=True)

    def delete_backup(self, path: Path) -> None:
        """Delete a backup. Only files inside the backups folder can be deleted."""
        resolved = path.resolve()
        if self._paths.backups_dir.resolve() not in resolved.parents or not resolved.is_file():
            raise InvalidConfigurationError("Only files in the backups folder can be deleted here.")
        resolved.unlink()
        log.info("backup_deleted", file=resolved.name)

    # -- restore ---------------------------------------------------------------- #
    def validate_backup(self, path: Path) -> str:
        """Check that ``path`` is a restorable backup; returns its schema revision."""
        if not path.is_file():
            raise InvalidConfigurationError("The backup file does not exist.")
        with path.open("rb") as handle:
            if handle.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                raise InvalidConfigurationError("This file is not a database backup.")
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise InvalidConfigurationError(
                        "The backup is damaged (integrity check failed)."
                    )
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                missing = REQUIRED_TABLES - tables
                if missing:
                    raise InvalidConfigurationError(
                        "This database was not created by this application "
                        f"(missing: {', '.join(sorted(missing))})."
                    )
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error as exc:
            raise InvalidConfigurationError(f"The backup could not be read: {exc}") from exc
        revision = str(row[0]) if row else ""
        if revision not in self._known_revisions():
            raise InvalidConfigurationError(
                "The backup comes from a newer version of the application and cannot be restored."
            )
        return revision

    def restore(self, path: Path) -> BackupInfo:
        """Replace the current database with ``path``. Returns the pre-restore safety copy."""
        if self._is_run_active():
            raise BackupError("Stop the running automation before restoring a backup.")
        self.validate_backup(path)
        safety_copy = self.create_backup("pre-restore")
        database_file = self._paths.database_file
        try:
            self._database.dispose()
            for suffix in ("-wal", "-shm"):
                Path(f"{database_file}{suffix}").unlink(missing_ok=True)
            shutil.copyfile(path, database_file)
            self._migrator.upgrade()  # a backup from an older version is brought up to date
            self._database.ping()
        except Exception as exc:
            log.exception("restore_failed")
            self._roll_back(safety_copy.path)
            raise BackupError(
                f"The backup could not be restored ({exc}). Your previous data was kept."
            ) from exc
        log.info("backup_restored", file=path.name, safety_copy=safety_copy.name)
        return safety_copy

    def _roll_back(self, safety_copy: Path) -> None:
        """Put the safety copy back after a failed restore."""
        self._database.dispose()
        database_file = self._paths.database_file
        for suffix in ("-wal", "-shm"):
            Path(f"{database_file}{suffix}").unlink(missing_ok=True)
        shutil.copyfile(safety_copy, database_file)

    # -- helpers ------------------------------------------------------------------ #
    def _known_revisions(self) -> set[str]:
        config = Config()
        config.set_main_option("script_location", self._paths.migrations_dir.as_posix())
        script = ScriptDirectory.from_config(config)
        return {revision.revision for revision in script.walk_revisions()}

    @staticmethod
    def _info(path: Path) -> BackupInfo:
        stat = path.stat()
        return BackupInfo(path, datetime.fromtimestamp(stat.st_mtime), stat.st_size)
