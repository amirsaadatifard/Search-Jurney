"""Alembic migrations must produce exactly the schema the ORM models describe."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect

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
    assert container.migrator.current_revision() == container.migrator.head_revision() == "0003"


# -- the Google search-source migration (0002 -> 0003) ------------------------------------ #
def legacy_scenario(connection: Connection, name: str, position: int | None) -> None:
    """Insert a scenario the way the pre-Google application would have written it."""
    now = "2026-01-01 10:00:00"
    connection.exec_driver_sql(
        """
        INSERT INTO target_sites (id, display_name, base_url, domain, environment, notes,
                                  enabled, approved_for_automation, created_at, updated_at)
        VALUES (1, 'Legacy site', 'https://shop.example/', 'shop.example', 'CONTROLLED_TEST', '',
                1, 1, ?, ?)
        """,
        (now, now),
    )
    connection.exec_driver_sql(
        """
        INSERT INTO browser_profiles (id, name, viewport_width, viewport_height, headless,
                                      locale, timezone, downloads_enabled, javascript_enabled,
                                      clear_storage, user_agent_mode, created_at, updated_at)
        VALUES (1, 'Legacy profile', 1280, 800, 0, '', '', 0, 1, 1, 'BROWSER_DEFAULT', ?, ?)
        """,
        (now, now),
    )
    connection.exec_driver_sql(
        f"""
        INSERT INTO scenarios (name, description, keyword, target_site_id, browser_profile_id,
                               search_profile, simulated_position, min_dwell_seconds,
                               max_dwell_seconds, iterations, delay_between_iterations,
                               scroll_enabled, max_scroll_actions, clear_cookies_between_sessions,
                               clear_storage_between_sessions, incognito, enabled,
                               created_at, updated_at)
        VALUES (?, '', 'mugs', 1, 1, 'LOCAL_SIMULATOR', ?, 5, 10, 2, 3, 1, 4, 1, 1, 1, 1,
                '{now}', '{now}')
        """,
        (name, position),
    )


def migrate_to(url: str, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


def test_the_google_migration_rewrites_scenarios_that_already_exist(tmp_path: Path) -> None:
    """Old rows carried a made-up rank; 0003 turns them into real-provider scenarios."""
    database = Database.for_file(tmp_path / "legacy.db")
    migrate_to(database.url, "0002")
    with database.engine.connect() as connection:
        legacy_scenario(connection, "Shop journey", 7)
        legacy_scenario(connection, "Demo - Local search journey", None)
        connection.commit()
    database.dispose()

    migrated = Database.for_file(tmp_path / "legacy.db")
    assert DatabaseMigrator(migrated, MIGRATIONS_DIR).upgrade() is True
    with migrated.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, search_profile FROM scenarios ORDER BY id"
        ).fetchall()
        schema = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
        ).scalar_one()
        indexes = {index["name"] for index in inspect(migrated.engine).get_indexes("scenarios")}
    migrated.dispose()

    assert [name for name, _ in rows] == ["Shop journey", "Demo - Sample site journey"]
    assert {profile for _, profile in rows} == {"GOOGLE"}
    assert "simulated_position" not in schema  # the invented rank left the table as well
    assert "ck_scenarios_simulated_position_range" not in schema
    assert "ck_scenarios_dwell_range_ordered" in schema  # the batch rebuild kept the other rules
    assert "ix_scenarios_keyword" in indexes  # ... and the indexes


def test_rolling_the_migration_back_restores_the_old_shape(tmp_path: Path) -> None:
    database = Database.for_file(tmp_path / "legacy.db")
    migrate_to(database.url, "0002")
    with database.engine.connect() as connection:
        legacy_scenario(connection, "Shop journey", 7)
        connection.commit()
    database.dispose()

    url = Database.for_file(tmp_path / "legacy.db").url
    migrate_to(url, "head")
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config, "0002")

    check = Database.for_file(tmp_path / "legacy.db")
    with check.engine.connect() as connection:
        position, profile = connection.exec_driver_sql(
            "SELECT simulated_position, search_profile FROM scenarios"
        ).one()
    check.dispose()
    assert (position, profile) == (7, "LOCAL_SIMULATOR")  # the old value comes back on a downgrade
