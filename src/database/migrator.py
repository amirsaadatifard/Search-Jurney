"""Programmatic Alembic runner used at application startup.

The configuration is built in code so the application does not depend on the
current working directory or on ``alembic.ini`` (that file only serves the
``alembic`` command line during development).
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from src.database.session import Database
from src.logging_config import get_logger

log = get_logger(__name__)

HEAD = "head"


class DatabaseMigrator:
    """Brings a database to the latest schema revision."""

    def __init__(self, database: Database, migrations_dir: Path) -> None:
        self._database = database
        self._migrations_dir = migrations_dir

    def _config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", self._migrations_dir.as_posix())
        # '%' is the interpolation character of the underlying ConfigParser.
        config.set_main_option("sqlalchemy.url", self._database.url.replace("%", "%%"))
        return config

    def current_revision(self) -> str | None:
        """Revision the database is at, or ``None`` if it was never migrated."""
        with self._database.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    def head_revision(self) -> str | None:
        """Newest revision available in the migrations folder."""
        return ScriptDirectory.from_config(self._config()).get_current_head()

    def is_up_to_date(self) -> bool:
        return self.current_revision() == self.head_revision()

    def upgrade(self) -> bool:
        """Apply pending migrations. Returns ``True`` if anything was applied."""
        before = self.current_revision()
        head = self.head_revision()
        if before == head:
            log.info("database_schema_current", revision=head)
            return False
        if before is None and self._has_unversioned_tables():
            # A database created outside Alembic cannot be upgraded safely.
            raise RuntimeError(
                "The database contains tables but no migration history. "
                "Back it up and remove it so a fresh one can be created."
            )
        with self._database.engine.begin() as connection:
            config = self._config()
            config.attributes["connection"] = connection
            command.upgrade(config, HEAD)
        log.info("database_migrated", from_revision=before, to_revision=head)
        return True

    def _has_unversioned_tables(self) -> bool:
        return bool(inspect(self._database.engine).get_table_names())
