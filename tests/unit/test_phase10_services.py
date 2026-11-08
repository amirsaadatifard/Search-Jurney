"""Configurable paths, .env editing, settings defaults, backup/restore, config transfer, demo."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from src.app.bootstrap import bootstrap, shutdown
from src.app.config import AppConfig
from src.app.container import AppContainer
from src.app.paths import PROJECT_ROOT, AppPaths
from src.domain.exceptions import InvalidConfigurationError
from src.networking.credentials import InMemoryCredentialStore
from src.services.backup_service import BackupError
from src.services.config_transfer_service import FORMAT, VERSION
from src.services.env_file import update_env_file
from src.services.first_run_service import DEMO_SCENARIO_NAME, DEMO_SITE_NAME
from src.services.settings_service import AutomationDefaults, SettingKey

KW = {"text": "extra keyword", "tags": ["x"]}


@pytest.fixture
def other(tmp_path: Path) -> Iterator[AppContainer]:
    """A second, empty application (the import target)."""
    paths = AppPaths.from_data_dir(tmp_path / "other", root=PROJECT_ROOT)
    paths.ensure_directories()
    config = AppConfig(data_dir=paths.data_dir, target_port=18866)
    instance = bootstrap(config, paths, InMemoryCredentialStore())
    yield instance
    shutdown(instance)


# -- config and paths ------------------------------------------------------------------ #
def test_paths_can_be_overridden_individually(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        database_file=tmp_path / "db" / "my.db",
        logs_dir=Path("relative-logs"),
        screenshots_dir=tmp_path / "shots",
    )
    paths = AppPaths.from_config(config, root=tmp_path)
    assert paths.database_file == tmp_path / "db" / "my.db"
    assert paths.logs_dir == tmp_path / "relative-logs" and paths.log_file.parent == paths.logs_dir
    assert paths.screenshots_dir == tmp_path / "shots"
    assert paths.backups_dir == tmp_path / "data" / "backups"  # not overridden
    paths.ensure_directories()
    assert (tmp_path / "db").is_dir() and (tmp_path / "shots").is_dir()


def test_defaults_without_overrides(tmp_path: Path) -> None:
    paths = AppPaths.from_config(AppConfig(data_dir=tmp_path / "d"), root=tmp_path)
    assert (
        paths.database_file.parent == paths.data_dir and paths.logs_dir == paths.data_dir / "logs"
    )


def test_overrides_are_read_from_the_environment() -> None:
    config = AppConfig.from_environment(
        {"SJAT_LOGS_DIR": "C:/somewhere/logs", "SJAT_DATABASE_FILE": "x.db"}
    )
    assert config.logs_dir == Path("C:/somewhere/logs") and config.database_file == Path("x.db")


# -- .env editing ---------------------------------------------------------------------- #
def test_env_file_preserves_everything_it_does_not_touch(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# my notes\nSJAT_TARGET_PORT=9000\nOTHER=keep me\nSJAT_LOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )
    assert update_env_file(
        env,
        {"SJAT_TARGET_PORT": "9200", "SJAT_DATA_DIR": "D:/data", "SJAT_LOG_LEVEL": None},
    )
    assert (
        env.read_text(encoding="utf-8")
        == "# my notes\nSJAT_TARGET_PORT=9200\nOTHER=keep me\nSJAT_DATA_DIR=D:/data\n"
    )
    assert not update_env_file(env, {"SJAT_TARGET_PORT": "9200"})  # nothing to change
    assert AppConfig.from_environment({}, env_file=env).target_port == 9200


def test_env_file_is_created_and_rejects_unsafe_input(tmp_path: Path) -> None:
    env = tmp_path / "new" / ".env"
    assert (
        update_env_file(env, {"SJAT_DATA_DIR": "D:/data"})
        and env.read_text(encoding="utf-8") == "SJAT_DATA_DIR=D:/data\n"
    )
    with pytest.raises(InvalidConfigurationError, match="cannot be changed"):
        update_env_file(env, {"PATH": "x"})
    with pytest.raises(InvalidConfigurationError, match="invalid characters"):
        update_env_file(env, {"SJAT_DATA_DIR": "a\nSJAT_LOG_LEVEL=DEBUG"})
    assert env.read_text(encoding="utf-8") == "SJAT_DATA_DIR=D:/data\n"
    assert not list(env.parent.glob(".env.*.tmp"))  # no temp files left behind


# -- automation defaults ------------------------------------------------------------------ #
def test_defaults_round_trip_and_feed_the_scenario_form(container: AppContainer) -> None:
    assert container.settings.automation_defaults() == AutomationDefaults()
    container.settings.save_automation_defaults(
        AutomationDefaults(
            min_dwell_seconds=3,
            max_dwell_seconds=9,
            timeout_seconds=45,
            screenshots={
                "target_opened": False,
                "session_end": True,
                "error": False,
            },
        )
    )
    saved = container.settings.automation_defaults()
    assert (saved.min_dwell_seconds, saved.max_dwell_seconds, saved.timeout_seconds) == (3, 9, 45)
    assert saved.screenshots["target_opened"] is False and saved.screenshots["error"] is False
    options = container.scenarios.form_options()
    assert (options.default_min_dwell, options.default_max_dwell) == (3, 9)
    plan = container.runs.build_plans([container.scenarios.search()[0].id])[0]
    assert plan.timeout_seconds == 45 and not plan.screenshots.target_opened


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"min_dwell_seconds": 0}, "min_dwell_seconds"),
        ({"min_dwell_seconds": 20, "max_dwell_seconds": 10}, "max_dwell_seconds"),
        ({"max_dwell_seconds": 99999}, "max_dwell_seconds"),
        ({"timeout_seconds": 1}, "timeout_seconds"),
        ({"timeout_seconds": 999}, "timeout_seconds"),
    ],
)
def test_invalid_defaults_are_rejected(
    container: AppContainer, overrides: dict[str, int], field: str
) -> None:
    values: dict[str, Any] = {
        "min_dwell_seconds": 5,
        "max_dwell_seconds": 15,
        "timeout_seconds": 30,
    } | overrides
    with pytest.raises(InvalidConfigurationError) as caught:
        container.settings.save_automation_defaults(AutomationDefaults(**values))
    assert field in caught.value.field_errors
    assert container.settings.automation_defaults() == AutomationDefaults()  # nothing was saved


def test_damaged_stored_values_fall_back_to_defaults(container: AppContainer) -> None:
    container.settings.set(SettingKey.DEFAULT_MIN_DWELL, "banana")
    container.settings.set(SettingKey.SCREENSHOT_POLICY, ["not", "a", "dict"])
    assert container.settings.automation_defaults() == AutomationDefaults()


# -- backup and restore ---------------------------------------------------------------------- #
def test_backup_is_a_consistent_listed_copy(container: AppContainer) -> None:
    info = container.backups.create_backup("Before the demo!")
    assert info.path.parent == container.paths.backups_dir and "Before-the-demo" in info.name
    assert (
        info.size_bytes > 0
        and container.backups.validate_backup(info.path) == container.migrator.head_revision()
    )
    second = container.backups.create_backup()
    assert next(b.name for b in container.backups.list_backups()) in {second.name, info.name}
    assert {b.name for b in container.backups.list_backups()} == {info.name, second.name}
    with closing(sqlite3.connect(info.path)) as conn:
        assert conn.execute("SELECT count(*) FROM scenarios").fetchone()[0] == 1


def test_restore_brings_back_the_backed_up_state(container: AppContainer) -> None:
    backup = container.backups.create_backup("clean")
    container.keywords.create(KW)
    container.scenarios.delete(container.scenarios.search()[0].id)
    assert len(container.keywords.search()) == 6 and container.scenarios.search() == []

    safety = container.backups.restore(backup.path)
    assert "pre-restore" in safety.name and safety.path.is_file()
    assert len(container.keywords.search()) == 5 and len(container.scenarios.search()) == 1
    # The safety copy holds what was there before the restore.
    with closing(sqlite3.connect(safety.path)) as conn:
        assert conn.execute("SELECT count(*) FROM keywords").fetchone()[0] == 6
    container.keywords.create(KW)  # the database is fully usable afterwards
    assert container.migrator.is_up_to_date()


def test_restore_is_refused_while_a_run_is_active(
    container: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = container.backups.create_backup()
    monkeypatch.setattr(container.runs, "_thread", type("T", (), {"is_alive": lambda self: True})())
    with pytest.raises(BackupError, match="Stop the running automation"):
        container.backups.restore(backup.path)


@pytest.fixture
def bad_files(tmp_path: Path, container: AppContainer) -> dict[str, Path]:
    files: dict[str, Path] = {}
    files["text"] = tmp_path / "notes.db"
    files["text"].write_text("this is not a database")
    files["foreign"] = tmp_path / "foreign.db"
    with closing(sqlite3.connect(files["foreign"])) as conn:
        conn.execute("CREATE TABLE users (id INTEGER)")
        conn.commit()
    files["newer"] = tmp_path / "newer.db"
    files["newer"].write_bytes(container.backups.create_backup().path.read_bytes())
    with closing(sqlite3.connect(files["newer"])) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'zzzz_future'")
        conn.commit()
    files["truncated"] = tmp_path / "truncated.db"
    files["truncated"].write_bytes(container.backups.create_backup().path.read_bytes()[:3000])
    return files


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("text", "not a database backup"),
        ("foreign", "not created by this application"),
        ("newer", "newer version"),
    ],
)
def test_unsuitable_files_are_refused_and_nothing_changes(
    container: AppContainer, bad_files: dict[str, Path], key: str, message: str
) -> None:
    with pytest.raises(InvalidConfigurationError, match=message):
        container.backups.restore(bad_files[key])
    assert len(container.scenarios.search()) == 1 and not [
        b for b in container.backups.list_backups() if "pre-restore" in b.name
    ]


def test_a_damaged_file_never_replaces_the_database(
    container: AppContainer, bad_files: dict[str, Path]
) -> None:
    with pytest.raises((InvalidConfigurationError, BackupError)):
        container.backups.restore(bad_files["truncated"])
    assert len(container.scenarios.search()) == 1 and container.database.ping()
    with pytest.raises(InvalidConfigurationError, match="does not exist"):
        container.backups.restore(bad_files["text"].parent / "missing.db")


def test_failed_restore_rolls_back_to_the_previous_data(
    container: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = container.backups.create_backup()
    container.keywords.create(KW)
    monkeypatch.setattr(
        container.migrator,
        "upgrade",
        lambda: (_ for _ in ()).throw(RuntimeError("migration exploded")),
    )
    with pytest.raises(BackupError, match="previous data was kept"):
        container.backups.restore(backup.path)
    assert (
        len(container.keywords.search()) == 6
    )  # the keyword added after the backup is still there


def test_only_backups_can_be_deleted(container: AppContainer, tmp_path: Path) -> None:
    backup = container.backups.create_backup()
    container.backups.delete_backup(backup.path)
    assert container.backups.list_backups() == []
    outside = tmp_path / "keep.db"
    outside.write_text("x")
    with pytest.raises(InvalidConfigurationError, match="backups folder"):
        container.backups.delete_backup(outside)
    with pytest.raises(InvalidConfigurationError):
        container.backups.delete_backup(container.paths.database_file)
    assert outside.exists() and container.paths.database_file.exists()


# -- configuration export / import ----------------------------------------------------------- #
def populate(container: AppContainer) -> None:
    from src.domain.schemas import ScenarioInput

    container.proxies.create(
        {"name": "Lab proxy", "host": "10.0.0.5", "port": 3128, "username": "u"}, "s3cret"
    )
    container.target_sites.create(
        {
            "display_name": "Staging",
            "base_url": "https://staging.shop.test",
            "environment": "STAGING",
        }
    )
    container.keywords.create({"text": "wheel thrown mug", "tags": ["a", "b"], "notes": "n"})
    scenario = container.scenarios.search()[0]
    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}
    container.scenarios.update(
        scenario.id, data | {"proxy_profile_id": container.proxies.list()[0].id}
    )
    container.settings.set(SettingKey.UI_THEME, "dark")
    container.settings.set(SettingKey.DEFAULT_MIN_DWELL, 4)


def test_export_never_contains_secrets_or_history(container: AppContainer, tmp_path: Path) -> None:
    populate(container)
    path = container.transfer.export_file(tmp_path / "config")
    text = path.read_text(encoding="utf-8")
    assert "s3cret" not in text and "password" not in text.replace("password_stored", "")
    data = json.loads(text)
    assert (data["format"], data["version"]) == (FORMAT, VERSION)
    assert data["proxy_profiles"][0] == {
        "name": "Lab proxy",
        "protocol": "HTTP",
        "host": "10.0.0.5",
        "port": 3128,
        "username": "u",
        "enabled": True,
        "notes": "",
        "password_stored": True,
    }
    assert data["settings"][SettingKey.UI_THEME] == "dark"
    scenario = data["scenarios"][0]
    assert scenario["target_site"] == DEMO_SITE_NAME and scenario["proxy_profile"] == "Lab proxy"
    assert "target_site_id" not in scenario and "sessions" not in data


def test_round_trip_into_a_fresh_installation(
    container: AppContainer, other: AppContainer, tmp_path: Path
) -> None:
    populate(container)
    # The target app starts with the same demo data (first run); remove it to see a clean import.
    other.scenarios.delete(other.scenarios.search()[0].id)
    for keyword in other.keywords.search():
        other.keywords.delete(keyword.id)
    path = container.transfer.export_file(tmp_path / "config.json")

    result = other.transfer.import_file(path)
    assert result.errors == [] and result.total_created >= 5
    assert {k.text for k in other.keywords.search()} >= {
        "wheel thrown mug",
        "handmade ceramic mugs",
    }
    assert other.keywords.search(text="wheel thrown")[0].tags == ["a", "b"]
    assert other.proxies.list()[0].name == "Lab proxy" and not other.proxies.list()[0].has_password
    assert other.proxies.get_password(other.proxies.list()[0].id) is None  # passwords do not travel
    assert (
        other.settings.get(SettingKey.UI_THEME) == "dark"
        and other.settings.get(SettingKey.DEFAULT_MIN_DWELL) == 4
    )
    assert any("password" in note.lower() for note in result.notes)

    scenario = next(s for s in other.scenarios.search() if s.name == DEMO_SCENARIO_NAME)
    assert scenario.proxy_profile is not None and scenario.proxy_profile.name == "Lab proxy"
    assert scenario.target_site.display_name == DEMO_SITE_NAME


def test_imported_target_sites_are_never_approved(
    container: AppContainer, other: AppContainer, tmp_path: Path
) -> None:
    other.scenarios.delete(other.scenarios.search()[0].id)
    other.target_sites.delete(other.target_sites.search()[0].id)
    path = container.transfer.export_file(tmp_path / "c.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["target_sites"][0]["approved_for_automation"] is True  # approved at the source
    result = other.transfer.import_file(path)
    site = other.target_sites.search()[0]
    assert not site.approved_for_automation and not other.safety.is_allowed(
        site.base_url.replace("18766", "18866")
    )
    scenario = other.scenarios.search()[0]
    assert scenario.enabled is False  # stored, but cannot run until the site is approved
    assert any("not approved" in note for note in result.notes)
    other.target_sites.set_approved(site.id, True)
    other.scenarios.set_enabled(scenario.id, True)


def test_existing_items_are_kept_unless_overwrite(container: AppContainer, tmp_path: Path) -> None:
    path = container.transfer.export_file(tmp_path / "c.json")
    container.keywords.create({"text": "unrelated"})
    again = container.transfer.import_file(path)
    assert again.total_created == 0 and again.skipped["keywords"] == 5 and again.errors == []
    assert len(container.keywords.search()) == 6

    data = json.loads(path.read_text(encoding="utf-8"))
    data["keywords"][0]["notes"] = "changed in file"
    data["scenarios"][0]["iterations"] = 7
    path.write_text(json.dumps(data), encoding="utf-8")
    kept = container.transfer.import_file(path)
    assert container.keywords.search(text="handmade")[0].notes != "changed in file"
    assert kept.total_updated == 0 and kept.errors == []  # nothing is overwritten by default
    overwritten = container.transfer.import_file(path, overwrite=True)
    assert overwritten.updated["scenarios"] == 1 and container.scenarios.search()[0].iterations == 7
    assert container.keywords.search(text=data["keywords"][0]["text"])[0].notes == "changed in file"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(format="other"), "not a Search Journey"),
        (lambda d: d.update(version=99), "Unsupported configuration version"),
        (lambda d: d.update(keywords="oops"), "must be a list"),
        (lambda d: d["keywords"].append({"text": ""}), "keywords #6"),
        (lambda d: d["browser_profiles"][0].update(viewport_width=10), "browser_profiles #1"),
        (
            lambda d: d["scenarios"][0].update(min_dwell_seconds=50, max_dwell_seconds=5),
            "scenarios #1",
        ),
        (lambda d: d["settings"].update({SettingKey.UI_THEME: "neon"}), "theme"),
        (lambda d: d["settings"].update({SettingKey.DEFAULT_TIMEOUT: 1}), "timeout"),
        (
            lambda d: d["settings"].update({SettingKey.SCREENSHOT_POLICY: {"evil": True}}),
            "screenshot policy",
        ),
    ],
)
def test_invalid_files_are_rejected_as_a_whole(
    container: AppContainer, other: AppContainer, tmp_path: Path, mutate: Any, message: str
) -> None:
    data = container.transfer.export_dict()
    mutate(data)
    before = (
        len(other.keywords.search()),
        len(other.scenarios.search()),
        other.settings.get(SettingKey.UI_THEME),
    )
    with pytest.raises(InvalidConfigurationError, match=message):
        other.transfer.import_dict(data)
    assert (
        len(other.keywords.search()),
        len(other.scenarios.search()),
        other.settings.get(SettingKey.UI_THEME),
    ) == before


def test_target_site_rules_still_apply_to_imports(
    container: AppContainer, other: AppContainer
) -> None:
    data = container.transfer.export_dict()
    data["target_sites"].append(
        {"display_name": "Local?", "base_url": "https://example.com", "environment": "LOCAL"}
    )
    result = other.transfer.import_dict(data)
    assert any("Local?" in e and "loopback" in e for e in result.errors)
    assert all(s.display_name != "Local?" for s in other.target_sites.search())


def test_scenarios_with_missing_references_are_reported(
    container: AppContainer, other: AppContainer
) -> None:
    data = container.transfer.export_dict()
    data["scenarios"][0].update(target_site="No such site", name="Broken one")
    other.scenarios.delete(other.scenarios.search()[0].id)
    result = other.transfer.import_dict(data)
    assert any("Broken one" in e and "does not exist" in e for e in result.errors)
    assert other.scenarios.search() == []


@pytest.mark.parametrize("content", ["not json {", "[1, 2]", '{"format": "x"}', ""])
def test_garbage_files(container: AppContainer, tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(InvalidConfigurationError):
        container.transfer.import_file(path)
    with pytest.raises(InvalidConfigurationError, match="does not exist"):
        container.transfer.import_file(tmp_path / "missing.json")


def test_oversized_files_are_refused(container: AppContainer, tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_bytes(b" " * (5 * 1024 * 1024 + 1))
    with pytest.raises(InvalidConfigurationError, match="larger than 5 MB"):
        container.transfer.import_file(path)


# -- demo ---------------------------------------------------------------------------- #
def test_demo_prepare_repairs_whatever_the_user_changed(container: AppContainer) -> None:
    site = container.target_sites.search()[0]
    scenario = container.scenarios.search()[0]
    container.scenarios.set_enabled(scenario.id, False)
    container.target_sites.set_approved(site.id, False)
    container.target_sites.set_enabled(site.id, False)
    assert container.demo.prepare() == scenario.id
    assert (
        container.scenarios.search()[0].enabled
        and container.target_sites.search()[0].is_automation_allowed
    )


def test_demo_prepare_recreates_deleted_data_and_follows_the_port(container: AppContainer) -> None:
    container.scenarios.delete(container.scenarios.search()[0].id)
    assert container.scenarios.search() == []
    scenario_id = container.demo.prepare()
    assert container.scenarios.get(scenario_id).name == DEMO_SCENARIO_NAME
    from src.database.repositories import TargetSiteRepository

    with container.database.session_scope() as session:
        site = TargetSiteRepository(session).get_by_name(DEMO_SITE_NAME)
        assert site is not None
        site.base_url = "http://127.0.0.1:9999/"  # as if the port had been changed
    container.demo.prepare()
    assert (
        container.target_sites.search()[0].base_url
        == f"http://127.0.0.1:{container.config.target_port}/"
    )


def test_demo_runs_exactly_one_cycle(container: AppContainer) -> None:
    scenario_id = container.demo.prepare()
    assert container.scenarios.get(scenario_id).iterations == 2
    plans = container.runs.build_plans([scenario_id], iterations_override=1)
    assert [p.iterations for p in plans] == [1]


def test_a_public_search_engine_in_a_file_is_refused_site_by_site(
    container: AppContainer, other: AppContainer
) -> None:
    data = container.transfer.export_dict()
    data["target_sites"].append(
        {"display_name": "Search", "base_url": "https://www.google.com", "environment": "STAGING"}
    )
    result = other.transfer.import_dict(data)
    assert any("Search" in e and "public search engine" in e for e in result.errors)
    assert all(s.display_name != "Search" for s in other.target_sites.search())
