"""ORM models, constraints and repositories."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.database.models import AutomationSession, Scenario, Screenshot, SessionEvent
from src.database.repositories import (
    BrowserProfileRepository,
    KeywordRepository,
    ProxyProfileRepository,
    ScenarioRepository,
    SessionFilter,
    SessionRepository,
    TargetSiteRepository,
)
from src.database.session import Database
from src.domain.enums import (
    Environment,
    EventType,
    ScreenshotKind,
    SessionState,
    SessionStatus,
)
from src.domain.exceptions import EntityNotFoundError
from tests.factories import (
    make_browser_profile,
    make_keyword,
    make_proxy_profile,
    make_scenario,
    make_session,
    make_target_site,
)


# -- generic behaviour --------------------------------------------------------- #
def test_add_assigns_id_and_require_raises_for_missing(database: Database) -> None:
    with database.session_scope() as session:
        repo = KeywordRepository(session)
        keyword = repo.add(make_keyword())
        assert keyword.id is not None
        assert repo.require(keyword.id) is keyword
        assert repo.count() == 1
        with pytest.raises(EntityNotFoundError):
            repo.require(9999)


def test_session_scope_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError), database.session_scope() as session:
        KeywordRepository(session).add(make_keyword())
        raise RuntimeError("boom")
    with database.session_scope() as session:
        assert KeywordRepository(session).count() == 0


def test_timestamps_and_enum_values_are_stored(database: Database) -> None:
    with database.session_scope() as session:
        site = TargetSiteRepository(session).add(make_target_site(environment=Environment.STAGING))
        site_id = site.id
        assert site.created_at is not None and site.updated_at is not None
    with database.engine.connect() as connection:
        raw = connection.exec_driver_sql(
            "SELECT environment FROM target_sites WHERE id = ?", (site_id,)
        ).scalar_one()
    assert raw == "STAGING"


# -- keywords ------------------------------------------------------------------ #
def test_keyword_text_is_unique(database: Database) -> None:
    with pytest.raises(IntegrityError), database.session_scope() as session:
        repo = KeywordRepository(session)
        repo.add(make_keyword("gold"))
        repo.add(make_keyword("gold"))


def test_keyword_search_filters_and_tags(database: Database) -> None:
    with database.session_scope() as session:
        repo = KeywordRepository(session)
        repo.add(make_keyword("Gold ring", tags=["jewellery", "Demo"]))
        repo.add(make_keyword("silver chain", tags=["jewellery"], enabled=False))
        repo.add(make_keyword("100% cotton", notes="textile"))

        assert [k.text for k in repo.search()] == ["100% cotton", "Gold ring", "silver chain"]
        assert [k.text for k in repo.search(text="GOLD")] == ["Gold ring"]
        assert [k.text for k in repo.search(text="textile")] == ["100% cotton"]
        assert [k.text for k in repo.search(text="%")] == ["100% cotton"]  # wildcard is literal
        assert [k.text for k in repo.search(enabled=False)] == ["silver chain"]
        assert [k.text for k in repo.search(tag="demo")] == ["Gold ring"]
        assert repo.all_tags() == ["Demo", "jewellery"]
        assert repo.get_by_text("  GOLD RING ") is not None
        assert repo.existing_texts(["gold ring", "new one"]) == {"gold ring"}


# -- target sites ---------------------------------------------------------------- #
def test_target_site_allowlist_requires_enabled_and_approved(database: Database) -> None:
    with database.session_scope() as session:
        repo = TargetSiteRepository(session)
        repo.add(make_target_site(display_name="Approved"))
        repo.add(make_target_site(display_name="Pending", approved_for_automation=False))
        repo.add(make_target_site(display_name="Disabled", enabled=False))

        assert [s.display_name for s in repo.list_automation_allowed()] == ["Approved"]
        assert len(repo.find_by_domain("127.0.0.1")) == 3
        assert [s.display_name for s in repo.search(approved=False)] == ["Pending"]
        assert [s.display_name for s in repo.search(text="dis")] == ["Disabled"]
        assert repo.search(environment=Environment.STAGING) == []


# -- profiles -------------------------------------------------------------------- #
def test_profile_lookup_and_sorting(database: Database) -> None:
    with database.session_scope() as session:
        browsers = BrowserProfileRepository(session)
        browsers.add(make_browser_profile(name="Mobile"))
        browsers.add(make_browser_profile(name="desktop"))
        assert [p.name for p in browsers.list_sorted()] == ["desktop", "Mobile"]
        assert browsers.get_by_name("MOBILE") is not None

        proxies = ProxyProfileRepository(session)
        proxies.add(make_proxy_profile(name="B", enabled=False))
        proxies.add(make_proxy_profile(name="A"))
        assert [p.name for p in proxies.list_sorted()] == ["A", "B"]
        assert [p.name for p in proxies.list_sorted(enabled_only=True)] == ["A"]


# -- scenarios ------------------------------------------------------------------- #
def _seed_scenarios(database: Database) -> None:
    with database.session_scope() as session:
        site = make_target_site()
        other_site = make_target_site(display_name="Staging Shop", domain="staging.example.test")
        browser = make_browser_profile()
        repo = ScenarioRepository(session)
        repo.add(make_scenario(site, browser, name="Alpha", keyword="gold ring", iterations=3))
        repo.add(make_scenario(site, browser, name="beta", keyword="silver", enabled=False))
        repo.add(make_scenario(other_site, browser, name="Gamma", keyword="watch", iterations=9))


def test_scenario_search_sort_and_filter(database: Database) -> None:
    _seed_scenarios(database)
    with database.session_scope() as session:
        repo = ScenarioRepository(session)
        assert [s.name for s in repo.search()] == ["Alpha", "beta", "Gamma"]
        assert [s.name for s in repo.search(descending=True)] == ["Gamma", "beta", "Alpha"]
        assert repo.search(sort_by="iterations", descending=True)[0].name == "Gamma"
        assert repo.search(sort_by="'; DROP TABLE scenarios")[0].name == "Alpha"
        assert [s.name for s in repo.search(text="SILV")] == ["beta"]
        assert [s.name for s in repo.search(text="staging")] == ["Gamma"]
        assert [s.name for s in repo.search(enabled=False)] == ["beta"]
        assert repo.count_enabled() == 2
        site_id = repo.get_by_name("alpha").target_site_id  # type: ignore[union-attr]
        assert repo.count_using_target_site(site_id) == 2
        assert [s.name for s in repo.search(target_site_id=site_id)] == ["Alpha", "beta"]


def test_scenario_relationships_are_usable_after_session_closes(database: Database) -> None:
    _seed_scenarios(database)
    with database.session_scope() as session:
        scenario = ScenarioRepository(session).get_by_name("Alpha")
    assert scenario is not None
    assert scenario.target_site.domain == "127.0.0.1"
    assert scenario.browser_profile.name == "Default Desktop"
    assert scenario.proxy_profile is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_dwell_seconds": 20, "max_dwell_seconds": 10},
        {"iterations": 0},
        {"min_dwell_seconds": -1},
        {"max_scroll_actions": -1},
        {"delay_between_iterations": -5},
    ],
)
def test_scenario_check_constraints_reject_invalid_rows(
    database: Database, overrides: dict[str, int]
) -> None:
    with pytest.raises(IntegrityError), database.session_scope() as session:
        ScenarioRepository(session).add(
            make_scenario(make_target_site(), make_browser_profile(), **overrides)
        )


def test_target_site_in_use_cannot_be_deleted(database: Database) -> None:
    _seed_scenarios(database)
    with pytest.raises(IntegrityError), database.session_scope() as session:
        repo = TargetSiteRepository(session)
        site = repo.get_by_name("Local Test Site")
        assert site is not None
        repo.delete(site)  # ON DELETE RESTRICT: two scenarios still reference it
    with database.session_scope() as session:
        assert ScenarioRepository(session).count() == 3


def test_deleting_proxy_clears_scenario_reference(database: Database) -> None:
    with database.session_scope() as session:
        proxy = ProxyProfileRepository(session).add(make_proxy_profile())
        scenario = ScenarioRepository(session).add(
            make_scenario(make_target_site(), make_browser_profile(), proxy_profile=proxy)
        )
        scenario_id, proxy_id = scenario.id, proxy.id
    with database.session_scope() as session:
        session.execute(
            ProxyProfileRepository.model.__table__.delete().where(
                ProxyProfileRepository.model.id == proxy_id
            )
        )
    with database.session_scope() as session:
        assert ScenarioRepository(session).require(scenario_id).proxy_profile_id is None


# -- sessions -------------------------------------------------------------------- #
def _seed_sessions(database: Database) -> None:
    base = datetime(2026, 1, 15, 10, 0, 0)
    with database.session_scope() as session:
        repo = SessionRepository(session)
        repo.add(make_session(base))
        repo.add(make_session(base + timedelta(hours=1), keyword="silver chain"))
        repo.add(make_session(base + timedelta(days=1), status=SessionStatus.FAILED))
        repo.add(
            make_session(
                base + timedelta(days=2),
                status=SessionStatus.TARGET_NOT_FOUND,
                target_domain="staging.example.test",
            )
        )


def test_session_filter_combinations(database: Database) -> None:
    _seed_sessions(database)
    day_one = datetime(2026, 1, 15)
    with database.session_scope() as session:
        repo = SessionRepository(session)
        assert repo.count_matching() == 4
        newest_first = repo.search()
        assert [s.started_at for s in newest_first] == sorted(
            (s.started_at for s in newest_first), reverse=True
        )
        in_day = SessionFilter(started_from=day_one, started_until=day_one + timedelta(days=1))
        assert repo.count_matching(in_day) == 2
        assert repo.count_matching(SessionFilter(status=SessionStatus.FAILED)) == 1
        assert repo.count_matching(SessionFilter(keyword="SILVER")) == 1
        assert repo.count_matching(SessionFilter(target_domain="staging")) == 1
        assert repo.count_matching(SessionFilter(scenario_id=123)) == 0
        assert len(repo.search(limit=2)) == 2
        assert len(repo.search(limit=2, offset=3)) == 1
        assert repo.distinct_keywords() == ["gold ring", "silver chain"]
        assert repo.distinct_target_domains() == ["127.0.0.1", "staging.example.test"]
        latest = repo.latest()
        assert latest is not None and latest.status is SessionStatus.TARGET_NOT_FOUND


def test_events_and_screenshots_cascade_with_session(database: Database) -> None:
    with database.session_scope() as session:
        repo = SessionRepository(session)
        record = repo.add(make_session())
        repo.add_event(record.id, EventType.SESSION_STARTED, state=SessionState.INITIALIZING)
        repo.add_event(record.id, EventType.TARGET_FOUND, "position=4", data={"position": 4})
        repo.add_screenshot(
            Screenshot(session_id=record.id, kind=ScreenshotKind.SESSION_END, file_path="a.png")
        )
        session_id = record.id

    with database.session_scope() as session:
        loaded = SessionRepository(session).get_with_details(session_id)
    assert loaded is not None
    assert [e.event_type for e in loaded.events] == [
        EventType.SESSION_STARTED,
        EventType.TARGET_FOUND,
    ]
    assert loaded.events[1].data == {"position": 4}
    assert loaded.screenshots[0].kind is ScreenshotKind.SESSION_END

    with database.session_scope() as session:
        repo = SessionRepository(session)
        repo.delete(repo.require(session_id))
    with database.session_scope() as session:
        assert session.scalars(select(SessionEvent)).all() == []
        assert session.scalars(select(Screenshot)).all() == []


def test_session_history_survives_scenario_deletion(database: Database) -> None:
    with database.session_scope() as session:
        scenario = ScenarioRepository(session).add(
            make_scenario(make_target_site(), make_browser_profile())
        )
        record = SessionRepository(session).add(
            make_session(scenario_id=scenario.id, settings_snapshot={"iterations": 1})
        )
        scenario_id, session_id = scenario.id, record.id
    with database.session_scope() as session:
        session.execute(Scenario.__table__.delete().where(Scenario.id == scenario_id))
    with database.session_scope() as session:
        kept = session.get(AutomationSession, session_id)
        assert kept is not None
        assert kept.scenario_id is None
        assert kept.scenario_name == "Demo Scenario"
        assert kept.settings_snapshot == {"iterations": 1}


def test_interrupted_sessions_are_closed(database: Database) -> None:
    start = datetime(2026, 1, 15, 10, 0, 0)
    with database.session_scope() as session:
        repo = SessionRepository(session)
        repo.add(
            make_session(
                start,
                status=SessionStatus.RUNNING,
                state=SessionState.DWELLING,
                finished_at=None,
                duration_seconds=None,
            )
        )
        repo.add(make_session(start))
        assert repo.mark_interrupted_sessions(start + timedelta(seconds=90)) == 1
        assert repo.mark_interrupted_sessions(start + timedelta(seconds=120)) == 0
        failed = repo.search(SessionFilter(status=SessionStatus.FAILED))
        assert len(failed) == 1
        assert failed[0].duration_seconds == 90
        assert failed[0].error_type == "InterruptedSession"
