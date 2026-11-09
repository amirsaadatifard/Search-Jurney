"""Alembic migrations must produce exactly the schema the ORM models describe."""

from __future__ import annotations

from pathlib import Path
from typing import Final

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
    assert container.migrator.current_revision() == container.migrator.head_revision() == "0005"


# -- the Google search-source migration (0002 -> 0003) ------------------------------------ #
SHARED_ROWS: Final = (
    """
    INSERT OR IGNORE INTO target_sites (id, display_name, base_url, domain, environment, notes,
                                        enabled, approved_for_automation, created_at, updated_at)
    VALUES (1, 'Legacy site', 'https://shop.example/', 'shop.example', 'CONTROLLED_TEST', '',
            1, 1, '2026-01-01 10:00:00', '2026-01-01 10:00:00')
    """,
    """
    INSERT OR IGNORE INTO browser_profiles (id, name, viewport_width, viewport_height, headless,
                                           locale, timezone, downloads_enabled, javascript_enabled,
                                           clear_storage, user_agent_mode, created_at, updated_at)
    VALUES (1, 'Legacy profile', 1280, 800, 0, '', '', 0, 1, 1, 'BROWSER_DEFAULT',
            '2026-01-01 10:00:00', '2026-01-01 10:00:00')
    """,
)


def shared_rows(connection: Connection) -> None:
    """The one site and the one profile every scenario row points at, written once.

    ``OR IGNORE``, because a test that stores two scenarios calls this twice and the
    two rows belong to the database rather than to one of its rows.
    """
    for statement in SHARED_ROWS:
        connection.exec_driver_sql(statement)


def legacy_scenario(connection: Connection, name: str, position: int | None) -> None:
    """Insert a scenario the way the pre-Google application would have written it."""
    now = "2026-01-01 10:00:00"
    shared_rows(connection)
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


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def migrate_to(url: str, revision: str) -> None:
    """Bring a database up to ``revision``, the way the application does at startup."""
    command.upgrade(_config(url), revision)


def roll_back_to(url: str, revision: str) -> None:
    """Take it down again - the half of a migration a user can also need."""
    command.downgrade(_config(url), revision)


def test_the_google_migration_rewrites_scenarios_that_already_exist(tmp_path: Path) -> None:
    """Old rows carried a made-up rank; 0003 turns them into real-provider scenarios."""
    database = Database.for_file(tmp_path / "legacy.db")
    migrate_to(database.url, "0002")
    with database.engine.connect() as connection:
        legacy_scenario(connection, "Shop journey", 7)
        legacy_scenario(connection, "Demo - Local search journey", None)
        connection.commit()
    database.dispose()

    # Up to 0004, which is where the column still exists: 0005 is the column's
    # disappearance, and it has a test of its own below.
    migrated = Database.for_file(tmp_path / "legacy.db")
    migrate_to(migrated.url, "0004")
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
    # 0003 made every row a real-provider scenario, 0004 then chose the channel
    # that needs no key for it.
    assert {profile for _, profile in rows} == {"GOOGLE_BROWSER"}
    assert "simulated_position" not in schema  # the invented rank left the table as well
    assert "ck_scenarios_simulated_position_range" not in schema
    assert "ck_scenarios_dwell_range_ordered" in schema  # the batch rebuild kept the other rules
    assert "ix_scenarios_keyword" in indexes  # ... and the indexes


def test_rolling_the_migration_back_restores_the_old_shape(tmp_path: Path) -> None:
    """The column and its rule come back; a number the upgrade deleted does not.

    A downgrade can only promise the shape. ``simulated_position`` is re-created
    empty, and the one row whose position the old application also knew from its own
    seed data - the demo scenario - is what 0003 writes back.
    """
    database = Database.for_file(tmp_path / "legacy.db")
    migrate_to(database.url, "0002")
    with database.engine.connect() as connection:
        legacy_scenario(connection, "Shop journey", 7)
        legacy_scenario(connection, "Demo - Local search journey", 4)
        connection.commit()
    database.dispose()

    url = Database.for_file(tmp_path / "legacy.db").url
    migrate_to(url, "head")
    roll_back_to(url, "0002")

    check = Database.for_file(tmp_path / "legacy.db")
    with check.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, simulated_position, search_profile FROM scenarios ORDER BY id"
        ).fetchall()
        schema = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
        ).scalar_one()
    check.dispose()
    assert [name for name, _, _ in rows] == ["Shop journey", "Demo - Local search journey"]
    assert {profile for _, _, profile in rows} == {"LOCAL_SIMULATOR"}
    assert [position for _, position, _ in rows] == [None, 4]
    assert "ck_scenarios_simulated_position_range" in schema


# -- the rank-source column goes away (0004 -> 0005) ------------------------------------ #
def test_dropping_the_rank_source_keeps_every_scenario(tmp_path: Path) -> None:
    """A scenario has nothing to choose any more, and nothing of it is lost.

    ``search_profile`` configured which channel read the ranking; the browser is now
    the only channel, so the column goes. The rows themselves must survive with
    every other value intact, which is what a user upgrading from an older database
    needs to be able to trust.
    """
    database = _rows_ready(tmp_path, "0004")
    with database.engine.connect() as connection:
        scenario_profile(connection, "Shop journey", "GOOGLE_BROWSER")
        scenario_profile(connection, "Chosen API", "GOOGLE_API")
        connection.commit()
    database.dispose()

    migrated = Database.for_file(tmp_path / "channels.db")
    assert DatabaseMigrator(migrated, MIGRATIONS_DIR).upgrade() is True
    with migrated.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, keyword, iterations, max_dwell_seconds FROM scenarios ORDER BY id"
        ).fetchall()
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(scenarios)").fetchall()
        }
    migrated.dispose()

    assert [row[0] for row in rows] == ["Shop journey", "Chosen API"]
    assert {row[1] for row in rows} == {"mugs"} and {row[2] for row in rows} == {2}
    assert "search_profile" not in columns


def test_the_dropped_column_comes_back_empty_on_a_downgrade(tmp_path: Path) -> None:
    """0005 can be undone: the column returns, holding the channel the app used."""
    database = _rows_ready(tmp_path, "0005")
    with database.engine.connect() as connection:
        connection.exec_driver_sql(
            "INSERT INTO scenarios (name, description, keyword, target_site_id, browser_profile_id,"
            " min_dwell_seconds, max_dwell_seconds, iterations, delay_between_iterations,"
            " scroll_enabled, max_scroll_actions, clear_cookies_between_sessions,"
            " clear_storage_between_sessions, incognito, enabled, created_at, updated_at)"
            " VALUES ('Shop journey', '', 'mugs', 1, 1, 5, 10, 2, 3, 1, 4, 1, 1, 1, 1,"
            " '2026-01-01 10:00:00', '2026-01-01 10:00:00')"
        )
        connection.commit()
    database.dispose()

    url = Database.for_file(tmp_path / "channels.db").url
    roll_back_to(url, "0004")

    check = Database.for_file(tmp_path / "channels.db")
    with check.engine.connect() as connection:
        profile = connection.exec_driver_sql("SELECT search_profile FROM scenarios").scalar_one()
    check.dispose()
    assert profile == "GOOGLE_BROWSER"


# -- the search-channel migration (0003 -> 0004) ------------------------------------------ #
def scenario_profile(connection: Connection, name: str, profile: str) -> None:
    """Insert one scenario row, the way a given revision would have written it."""
    connection.exec_driver_sql(
        "INSERT INTO scenarios (name, description, keyword, target_site_id, browser_profile_id,"
        " search_profile, min_dwell_seconds, max_dwell_seconds, iterations,"
        " delay_between_iterations, scroll_enabled, max_scroll_actions,"
        " clear_cookies_between_sessions, clear_storage_between_sessions, incognito, enabled,"
        " created_at, updated_at)"
        " VALUES (?, '', 'mugs', 1, 1, ?, 5, 10, 2, 3, 1, 4, 1, 1, 1, 1,"
        " '2026-01-01 10:00:00', '2026-01-01 10:00:00')",
        (name, profile),
    )


def _rows_ready(tmp_path: Path, revision: str) -> Database:
    """A database at ``revision``, with the two profiles a scenario needs."""
    database = Database.for_file(tmp_path / "channels.db")
    migrate_to(database.url, revision)
    with database.engine.connect() as connection:
        shared_rows(connection)
        connection.commit()
    return database


def test_the_channel_migration_moves_existing_scenarios_to_the_key_free_channel(
    tmp_path: Path,
) -> None:
    """A scenario stored when only one channel existed needs no key, so it gets none."""
    database = _rows_ready(tmp_path, "0003")
    with database.engine.connect() as connection:
        scenario_profile(connection, "Shop journey", "GOOGLE")
        scenario_profile(connection, "Chosen API", "GOOGLE_API")
        connection.commit()
    database.dispose()

    # 0003 only gave every row the provider's name; 0004 is what chooses a channel.
    migrate_to(Database.for_file(tmp_path / "channels.db").url, "0004")

    migrated = Database.for_file(tmp_path / "channels.db")
    with migrated.engine.connect() as connection:
        profiles = dict(
            connection.exec_driver_sql("SELECT name, search_profile FROM scenarios").fetchall()
        )
        schema = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='scenarios'"
        ).scalar_one()
    migrated.dispose()

    assert profiles == {"Shop journey": "GOOGLE_BROWSER", "Chosen API": "GOOGLE_API"}
    assert "simulated_position" not in schema  # the rebuild carried 0003's work forward
    assert "ck_scenarios_dwell_range_ordered" in schema


def test_a_scenario_that_chose_the_api_keeps_it_through_a_downgrade(tmp_path: Path) -> None:
    """Downgrading loses the *choice*, not the data: every row goes back to one channel."""
    database = _rows_ready(tmp_path, "0004")
    with database.engine.connect() as connection:
        scenario_profile(connection, "Chosen API", "GOOGLE_API")
        connection.commit()
    database.dispose()

    url = Database.for_file(tmp_path / "channels.db").url
    roll_back_to(url, "0003")

    check = Database.for_file(tmp_path / "channels.db")
    with check.engine.connect() as connection:
        profile = connection.exec_driver_sql("SELECT search_profile FROM scenarios").scalar_one()
    check.dispose()
    assert profile == "GOOGLE"
