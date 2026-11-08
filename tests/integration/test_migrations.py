"""Alembic migrations must produce exactly the schema the ORM models describe."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from src.app.container import AppContainer
from src.app.paths import PROJECT_ROOT
from src.database import models  # noqa: F401
from src.database.base import Base
from src.database.migrator import DatabaseMigrator
from src.database.session import Database

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


@pytest.fixture
def file_database(tmp_path: Path) -> Database:
    return Database.for_file(tmp_path / "migrated.db")


def test_upgrade_creates_every_table_and_is_idempotent(file_database: Database) -> None:
    migrator = DatabaseMigrator(file_database, MIGRATIONS_DIR)
    assert migrator.current_revision() is None
    assert migrator.upgrade() is True
    assert migrator.is_up_to_date()
    assert migrator.upgrade() is False

    tables = set(inspect(file_database.engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables
    file_database.dispose()


def test_migrated_schema_matches_models(file_database: Database) -> None:
    DatabaseMigrator(file_database, MIGRATIONS_DIR).upgrade()
    with file_database.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    file_database.dispose()
    assert differences == [], f"models and migrations have drifted: {differences}"


def test_unversioned_database_is_refused(file_database: Database) -> None:
    Base.metadata.create_all(file_database.engine)
    with pytest.raises(RuntimeError, match="no migration history"):
        DatabaseMigrator(file_database, MIGRATIONS_DIR).upgrade()
    file_database.dispose()


def test_foreign_keys_are_enforced(file_database: Database) -> None:
    with file_database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    file_database.dispose()


def test_bootstrap_migrates_the_application_database(container: AppContainer) -> None:
    assert container.migrator.is_up_to_date()
    assert container.migrator.current_revision() == container.migrator.head_revision() == "0002"
