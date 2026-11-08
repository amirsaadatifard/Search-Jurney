"""Validation schemas and the keyword / target-site / profile / scenario services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.app.container import AppContainer
from src.domain.enums import Environment, SearchProfile
from src.domain.exceptions import EntityInUseError, EntityNotFoundError, InvalidConfigurationError
from src.domain.schemas import (
    BrowserProfileInput,
    KeywordInput,
    ScenarioInput,
    validate_input,
)
from src.services.first_run_service import (
    DEMO_BROWSER_NAME,
    DEMO_KEYWORDS,
    DEMO_SCENARIO_NAME,
    DEMO_SITE_NAME,
    FirstRunService,
)


def field_errors(call: Any, *args: Any) -> dict[str, str]:
    with pytest.raises(InvalidConfigurationError) as error:
        call(*args)
    return error.value.field_errors


def scenario_data(container: AppContainer, **overrides: Any) -> dict[str, Any]:
    demo = container.scenarios.search()[0]
    data: dict[str, Any] = {
        "name": "Checkout journey",
        "keyword": "ceramic mugs",
        "target_site_id": demo.target_site_id,
        "browser_profile_id": demo.browser_profile_id,
        "min_dwell_seconds": 5,
        "max_dwell_seconds": 10,
        "iterations": 3,
    }
    return data | overrides


# -- schemas -------------------------------------------------------------------- #
def test_scenario_schema_accepts_valid_input_and_applies_defaults() -> None:
    values = validate_input(
        ScenarioInput,
        {
            "name": "  My scenario ",
            "keyword": "gold   ring",
            "target_site_id": 1,
            "browser_profile_id": 1,
            "min_dwell_seconds": 5,
            "max_dwell_seconds": 5,
        },
    )
    assert values.name == "My scenario"
    assert values.keyword == "gold ring"
    assert (values.iterations, values.search_profile, values.scroll_enabled) == (
        1,
        SearchProfile.GOOGLE_BROWSER,  # the channel that needs no key is the default
        True,
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name": "   "}, "name"),
        ({"name": "x" * 121}, "name"),
        ({"keyword": ""}, "keyword"),
        ({"keyword": "two\nlines"}, "keyword"),
        ({"min_dwell_seconds": 0}, "min_dwell_seconds"),
        ({"min_dwell_seconds": 20, "max_dwell_seconds": 10}, "max_dwell_seconds"),
        ({"max_dwell_seconds": 99999}, "max_dwell_seconds"),
        ({"iterations": 0}, "iterations"),
        ({"iterations": "many"}, "iterations"),
        ({"delay_between_iterations": -1}, "delay_between_iterations"),
        ({"max_scroll_actions": 500}, "max_scroll_actions"),
        ({"target_site_id": None}, "target_site_id"),
        ({"search_profile": "LOCAL_SIMULATOR"}, "search_profile"),  # the removed value is refused
        ({"unknown_field": 1}, "unknown_field"),
    ],
)
def test_scenario_schema_reports_the_offending_field(overrides: dict[str, Any], field: str) -> None:
    data = {
        "name": "ok",
        "keyword": "ok",
        "target_site_id": 1,
        "browser_profile_id": 1,
        "min_dwell_seconds": 5,
        "max_dwell_seconds": 10,
    } | overrides
    assert field in field_errors(validate_input, ScenarioInput, data)


def test_dwell_range_boundaries() -> None:
    base = {"name": "n", "keyword": "k", "target_site_id": 1, "browser_profile_id": 1}
    ok = validate_input(ScenarioInput, base | {"min_dwell_seconds": 1, "max_dwell_seconds": 3600})
    assert (ok.min_dwell_seconds, ok.max_dwell_seconds) == (1, 3600)
    errors = field_errors(
        validate_input, ScenarioInput, base | {"min_dwell_seconds": 11, "max_dwell_seconds": 10}
    )
    assert errors == {"max_dwell_seconds": "Maximum dwell time must not be less than the minimum."}


def test_missing_required_fields_get_friendly_messages() -> None:
    errors = field_errors(validate_input, ScenarioInput, {})
    assert errors["name"] == "This field is required."
    assert errors["min_dwell_seconds"] == "This field is required."


def test_keyword_schema_normalises_tags() -> None:
    values = validate_input(
        KeywordInput, {"text": " Gold ring ", "tags": [" demo", "Demo", "", "sale "]}
    )
    assert values.text == "Gold ring"
    assert values.tags == ["demo", "sale"]
    assert "tags" in field_errors(validate_input, KeywordInput, {"text": "x", "tags": ["a,b"]})
    assert "tags" in field_errors(
        validate_input, KeywordInput, {"text": "x", "tags": [f"t{i}" for i in range(11)]}
    )


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"viewport_width": 100}, "viewport_width"),
        ({"viewport_height": 9000}, "viewport_height"),
        ({"locale": "english"}, "locale"),
        ({"timezone": "Mars/Olympus_Mons"}, "timezone"),
        ({"timezone": "not a zone"}, "timezone"),
        ({"user_agent_mode": "IPHONE"}, "user_agent_mode"),
    ],
)
def test_browser_profile_schema_rejects_invalid_values(
    overrides: dict[str, Any], field: str
) -> None:
    assert field in field_errors(validate_input, BrowserProfileInput, {"name": "p"} | overrides)


def test_browser_profile_schema_accepts_locale_and_timezone() -> None:
    values = validate_input(
        BrowserProfileInput, {"name": "p", "locale": "en-US", "timezone": "Europe/Berlin"}
    )
    assert (values.locale, values.timezone) == ("en-US", "Europe/Berlin")


# -- first run -------------------------------------------------------------------- #
def test_first_run_seeds_demo_data_once(container: AppContainer) -> None:
    assert [s.name for s in container.scenarios.search()] == [DEMO_SCENARIO_NAME]
    assert [s.display_name for s in container.target_sites.search()] == [DEMO_SITE_NAME]
    assert [p.name for p in container.browser_profiles.list()] == [DEMO_BROWSER_NAME]
    assert len(container.keywords.search()) == len(DEMO_KEYWORDS)

    again = FirstRunService(container.database, container.settings, container.config)
    assert again.run_if_needed() is False
    assert len(container.scenarios.search()) == 1


def test_demo_site_is_on_the_allowlist(container: AppContainer) -> None:
    port = container.config.target_port
    assert container.safety.is_allowed(f"http://127.0.0.1:{port}/products")
    assert not container.safety.is_allowed(f"http://127.0.0.1:{port - 1}/")  # no other local port
    assert not container.safety.is_allowed("http://127.0.0.1:9/")
    assert not container.safety.is_allowed("https://example.com/")


# -- keywords -------------------------------------------------------------------- #
def test_keyword_crud_and_duplicates(container: AppContainer) -> None:
    service = container.keywords
    created = service.create({"text": "Blue vase", "tags": ["decor"], "notes": "n"})
    assert created.id is not None
    assert "text" in field_errors(service.create, {"text": "blue VASE"})

    updated = service.update(created.id, {"text": "Blue vase XL", "tags": []})
    assert (updated.text, updated.tags) == ("Blue vase XL", [])
    other = service.create({"text": "Red vase"})
    assert "text" in field_errors(service.update, other.id, {"text": "blue vase xl"})

    service.set_enabled(created.id, False)
    assert [k.text for k in service.search(enabled=False)] == ["Blue vase XL"]
    service.delete(created.id)
    assert service.search(text="blue") == []
    with pytest.raises(EntityNotFoundError):
        service.delete(created.id)


def test_bulk_add_skips_duplicates_and_invalid_entries(container: AppContainer) -> None:
    result = container.keywords.bulk_add(
        ["tea pot", "Tea Pot", "  ", "x" * 300, "sugar bowl", DEMO_KEYWORDS[0][0]], tags=["bulk"]
    )
    assert (result.added, result.duplicates, len(result.invalid)) == (2, 2, 1)
    assert result.summary() == "2 added, 2 already existed, 1 invalid"
    assert [k.text for k in container.keywords.search(tag="bulk")] == ["sugar bowl", "tea pot"]


def test_keyword_export_and_import_round_trip(container: AppContainer, tmp_path: Path) -> None:
    service = container.keywords
    service.create({"text": 'vase, "large"', "tags": ["a", "b"], "notes": "x", "enabled": False})
    export_path = tmp_path / "keywords.csv"
    exported = service.export_csv(export_path)
    assert exported == len(DEMO_KEYWORDS) + 1

    for keyword in service.search():
        service.delete(keyword.id)
    result = service.import_file(export_path)
    assert (result.added, result.duplicates, result.invalid) == (exported, 0, [])
    restored = service.search(text="vase")[0]
    assert (restored.text, restored.tags, restored.notes, restored.enabled) == (
        'vase, "large"',
        ["a", "b"],
        "x",
        False,
    )


def test_keyword_import_txt_and_plain_csv(container: AppContainer, tmp_path: Path) -> None:
    txt = tmp_path / "list.txt"
    txt.write_text("\ufeffalpha\n\n beta \nalpha\n", encoding="utf-8")
    result = container.keywords.import_file(txt)
    assert (result.added, result.duplicates) == (2, 1)

    plain = tmp_path / "plain.csv"
    plain.write_text("gamma,ignored\ndelta\n", encoding="utf-8")
    assert container.keywords.import_file(plain).added == 2
    assert [k.text for k in container.keywords.search(text="a")][:2] == ["alpha", "beta"]


def test_keyword_import_rejects_bad_files(container: AppContainer, tmp_path: Path) -> None:
    script = tmp_path / "keywords.py"
    script.write_text("print('x')", encoding="utf-8")
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"\xff\xfe\x00\x81")
    for path in (script, tmp_path / "missing.txt", binary):
        with pytest.raises(InvalidConfigurationError):
            container.keywords.import_file(path)


# -- target sites -------------------------------------------------------------------- #
def test_target_site_create_normalises_url_and_derives_domain(container: AppContainer) -> None:
    site = container.target_sites.create(
        {
            "display_name": "Staging shop",
            "base_url": "HTTPS://Staging.Shop.Test",
            "environment": Environment.STAGING,
        }
    )
    assert (site.base_url, site.domain) == ("https://staging.shop.test/", "staging.shop.test")
    assert site.approved_for_automation is False
    assert not container.safety.is_allowed("https://staging.shop.test/")

    container.target_sites.set_approved(site.id, True)
    assert container.safety.is_allowed("https://staging.shop.test/cart")
    container.target_sites.set_enabled(site.id, False)
    assert not container.safety.is_allowed("https://staging.shop.test/cart")


@pytest.mark.parametrize(
    ("base_url", "environment"),
    [
        ("https://www.google.com", Environment.STAGING),
        ("https://bing.com/search", Environment.CONTROLLED_TEST),
        ("file:///C:/site/index.html", Environment.LOCAL),
        ("ftp://127.0.0.1/", Environment.LOCAL),
        ("http://user:pw@127.0.0.1/", Environment.LOCAL),
        ("https://example.com/", Environment.LOCAL),
        ("not a url", Environment.DEVELOPMENT),
    ],
)
def test_target_site_rejects_unsafe_addresses(
    container: AppContainer, base_url: str, environment: Environment
) -> None:
    errors = field_errors(
        container.target_sites.create,
        {"display_name": "Bad", "base_url": base_url, "environment": environment},
    )
    assert "base_url" in errors
    assert container.target_sites.search(text="Bad") == []


def test_target_site_names_are_unique_and_in_use_sites_are_protected(
    container: AppContainer,
) -> None:
    demo = container.target_sites.search()[0]
    errors = field_errors(
        container.target_sites.create,
        {"display_name": DEMO_SITE_NAME.upper(), "base_url": "http://localhost:9000"},
    )
    assert "display_name" in errors
    with pytest.raises(EntityInUseError):
        container.target_sites.delete(demo.id)
    assert container.target_sites.search() != []


# -- browser profiles -------------------------------------------------------------------- #
def test_browser_profile_crud(container: AppContainer) -> None:
    service = container.browser_profiles
    profile = service.create({"name": "Small laptop", "viewport_width": 1024, "headless": True})
    assert (profile.viewport_width, profile.viewport_height, profile.headless) == (1024, 800, True)
    assert "name" in field_errors(service.create, {"name": "small LAPTOP"})
    assert service.update(profile.id, {"name": "Small laptop", "locale": "de"}).locale == "de"
    assert [p.name for p in service.list("small")] == ["Small laptop"]
    service.delete(profile.id)

    demo = service.list()[0]
    with pytest.raises(EntityInUseError):
        service.delete(demo.id)


# -- scenarios -------------------------------------------------------------------- #
def test_scenario_create_update_and_relationships(container: AppContainer) -> None:
    service = container.scenarios
    scenario = service.create(scenario_data(container))
    assert scenario.target_site.display_name == DEMO_SITE_NAME
    assert scenario.browser_profile.name == DEMO_BROWSER_NAME

    # A file exported before the channels existed names "GOOGLE"; that meant the
    # only way there was, which is now the browser channel.
    updated = service.update(
        scenario.id, scenario_data(container, iterations=7, search_profile="GOOGLE")
    )
    assert (updated.iterations, updated.search_profile) == (7, SearchProfile.GOOGLE_BROWSER.value)
    updated = service.update(
        scenario.id, scenario_data(container, iterations=7, search_profile="GOOGLE_API")
    )
    assert updated.search_profile is SearchProfile.GOOGLE_API
    assert service.get(scenario.id).iterations == 7
    assert "name" in field_errors(service.create, scenario_data(container))
    assert "name" in field_errors(
        service.update, scenario.id, scenario_data(container, name=DEMO_SCENARIO_NAME)
    )


def test_scenario_requires_an_allowlisted_target(container: AppContainer) -> None:
    pending = container.target_sites.create(
        {"display_name": "Pending site", "base_url": "http://localhost:9100"}
    )
    errors = field_errors(
        container.scenarios.create, scenario_data(container, target_site_id=pending.id)
    )
    assert "not on the allowlist" in errors["target_site_id"]

    container.target_sites.set_approved(pending.id, True)
    scenario = container.scenarios.create(scenario_data(container, target_site_id=pending.id))

    container.target_sites.set_approved(pending.id, False)
    container.scenarios.set_enabled(scenario.id, False)
    with pytest.raises(InvalidConfigurationError, match="not approved"):
        container.scenarios.set_enabled(scenario.id, True)


def test_scenario_reports_missing_references(container: AppContainer) -> None:
    errors = field_errors(
        container.scenarios.create,
        scenario_data(container, target_site_id=999, browser_profile_id=999, proxy_profile_id=999),
    )
    assert set(errors) == {"target_site_id", "browser_profile_id", "proxy_profile_id"}


def test_scenario_duplicate_gets_unique_name_and_starts_disabled(container: AppContainer) -> None:
    service = container.scenarios
    original = service.create(scenario_data(container, iterations=4, max_scroll_actions=9))
    first = service.duplicate(original.id)
    second = service.duplicate(original.id)
    assert first.name == "Checkout journey (copy)"
    assert second.name == "Checkout journey (copy 2)"
    assert (first.iterations, first.max_scroll_actions, first.keyword) == (4, 9, "ceramic mugs")
    assert first.enabled is False
    assert first.target_site.display_name == DEMO_SITE_NAME
    assert len(service.search(text="checkout")) == 3

    service.delete(first.id)
    assert len(service.search(text="checkout")) == 2


def test_scenario_form_options_and_search(container: AppContainer) -> None:
    options = container.scenarios.form_options()
    assert [s.display_name for s in options.target_sites] == [DEMO_SITE_NAME]
    assert [p.name for p in options.browser_profiles] == [DEMO_BROWSER_NAME]
    assert options.proxy_profiles == []
    assert DEMO_KEYWORDS[0][0] in options.keywords

    container.scenarios.create(scenario_data(container, enabled=False))
    assert [s.name for s in container.scenarios.search(enabled=False)] == ["Checkout journey"]
    assert [s.name for s in container.scenarios.search(sort_by="iterations", descending=True)] == [
        "Checkout journey",
        DEMO_SCENARIO_NAME,
    ]
