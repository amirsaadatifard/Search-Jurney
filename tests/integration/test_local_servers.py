"""The bundled sample website, the search-page double and the server manager.

Two servers matter to a journey: the sample site, which this application owns,
and the results page it browses, which it does not. This file proves both halves -
and that the page double really is laid out like Google's, so the shipped parser
reads it without a browser in sight.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app.constants import LOOPBACK_HOST
from src.app.container import AppContainer
from src.app.health import HealthStatus, is_port_available
from src.automation.google_search_page import PageKind, SearchSite, find_target, parse_results
from src.automation.models import MODE_BROWSER, MODE_DIRECT
from src.domain.enums import Environment
from src.domain.schemas import ScenarioInput
from src.local_servers.common import SECURITY_HEADERS
from src.local_servers.server_manager import LocalServer, LocalServerManager, ServerStartError
from src.local_servers.target_site import content
from src.local_servers.target_site.app import (
    EXTERNAL_REDIRECT_PATH,
    EXTERNAL_TEST_URL,
    PAGE_PATHS,
    create_target_app,
)
from src.local_servers.target_site.app import (
    SERVICE_NAME as TARGET_SERVICE_NAME,
)
from tests.helpers.fake_google_page import FakeGooglePages, listing


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def target() -> TestClient:
    return TestClient(create_target_app(), follow_redirects=False)


@pytest.fixture
def manager() -> Iterator[LocalServerManager]:
    instance = LocalServerManager(free_port())
    yield instance
    instance.stop_all()


# -- the sample website ---------------------------------------------------------- #
@pytest.mark.parametrize("path", PAGE_PATHS)
def test_every_target_page_renders_with_navigation(target: TestClient, path: str) -> None:
    response = target.get(path)
    assert response.status_code == 200
    html = response.text
    assert content.SITE_NAME.replace("&", "&amp;") in html
    assert 'id="main-nav"' in html
    for link in content.NAVIGATION:
        assert f'data-nav="{link.path}"' in html
    assert f'data-nav="{path}" aria-current="page"' in html
    assert "<h1" in html
    assert "Demo website for automated testing" in html
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_target_pages_have_long_scrollable_content(target: TestClient) -> None:
    home = target.get("/").text
    assert home.count("<section") >= 6
    assert home.count('class="placeholder"') >= 6

    products = target.get("/products").text
    assert products.count("data-product=") == len(content.PRODUCTS) == 12

    article = target.get("/article").text
    assert article.count("<p>") >= 18
    for section in content.ARTICLE_SECTIONS:
        assert f'id="{section.anchor}"' in article
    assert len(re.sub(r"<[^>]+>", " ", article).split()) > 800


def test_target_site_health_and_missing_page(target: TestClient) -> None:
    assert target.get("/health").json()["status"] == "ok"
    assert target.get("/does-not-exist").status_code == 404


def test_external_redirect_fixture_points_outside_the_allowlist(
    target: TestClient, container: AppContainer
) -> None:
    response = target.get(EXTERNAL_REDIRECT_PATH)
    assert response.status_code == 302
    assert response.headers["location"] == EXTERNAL_TEST_URL
    assert not container.safety.is_allowed(EXTERNAL_TEST_URL)


# -- the search-page double ------------------------------------------------------ #
def the_double_as_a_site(fake_google: FakeGooglePages) -> SearchSite:
    """The reading code pointed at the double, exactly as a journey points it at Google.

    :class:`SearchSite` is what the search step really uses: an entry page, and the
    answer to "which page is this?". Building one from the fixture's own origin is
    the whole of the substitution - no Google URL appears anywhere in the test.
    """
    return SearchSite(entry=fake_google.origin + "/")


def test_the_double_serves_pages_the_parser_reads(fake_google: FakeGooglePages) -> None:
    """The shipped reading code, pointed at a real HTTP response, without a browser.

    The e2e suite proves Chromium can walk these pages; this proves the *pure* half
    of the search step agrees with what the double writes out - same URL, same
    numbering, same answer - so a failing e2e test can be told apart from a failing
    fixture.
    """
    import httpx

    fake_google.set_results(listing("https://shop.example/", positions=(2,), total=4))
    with httpx.Client(base_url=fake_google.origin, timeout=5) as client:
        home = client.get("/")
        results = client.get("/search", params={"q": "handmade mugs"})
    assert home.status_code == 200 and fake_google.origin in fake_google.scope()["entry"]
    assert the_double_as_a_site(fake_google).classify(str(results.url), results.text) is (
        PageKind.SEARCH
    )
    assert 'div class="g"' in results.text  # the block the result selectors match


def test_the_double_lists_the_target_where_a_test_said_it_would(
    fake_google: FakeGooglePages,
) -> None:
    """Position 2 is position 2, in the HTML and in the parsed list alike."""
    import httpx

    fake_google.set_results(listing("https://shop.example/", positions=(2,), total=4))
    with httpx.Client(base_url=fake_google.origin, timeout=5) as client:
        page = client.get("/search", params={"q": "handmade mugs"})
    html_items = re.findall(r"<a href=\"([^\"]+)\"", page.text)
    results = parse_results([(title, link) for title, link in fake_google.results])
    assert "https://shop.example/" in html_items
    assert find_target(results, "shop.example") is not None
    assert find_target(results, "shop.example").position == 2
    assert [item.position for item in results] == [1, 2, 3, 4]


def test_a_walled_double_classifies_as_a_wall_not_as_no_results(
    fake_google: FakeGooglePages,
) -> None:
    """A refusal has to look like a refusal to the code that reports it."""
    import httpx

    fake_google.mode = "blocked"
    with httpx.Client(base_url=fake_google.origin, timeout=5) as client:
        page = client.get("/search", params={"q": "handmade mugs"})
    assert the_double_as_a_site(fake_google).classify(str(page.url), page.text) is (
        PageKind.BLOCKED
    )


def test_the_doubles_scope_and_the_guard_agree(fake_google: FakeGooglePages) -> None:
    """The fixture's own scope is what lets its pages load - and only those."""
    from src.services.safety_service import DomainSafetyService, SearchScope

    safety = DomainSafetyService(
        allowlist_provider=lambda: [], search_scope=SearchScope(**fake_google.scope())
    )
    fake_google.set_results(listing("https://shop.example/", positions=(1,), total=1))
    assert safety.is_search_page(f"{fake_google.origin}/search?q=mugs") is True
    assert safety.is_search_page(f"{fake_google.origin}/elsewhere") is False
    assert safety.is_search_page("https://www.google.com/search?q=mugs") is False
    assert safety.is_allowed("https://shop.example/") is False  # the scope is not an approval


# -- server manager (real sockets) ------------------------------------------------ #
def test_manager_serves_the_sample_site_over_http_and_stops_cleanly(
    manager: LocalServerManager,
) -> None:
    assert manager.start_all() == []
    assert manager.all_running
    assert manager.is_serving_port(manager.target.port)
    assert manager.start_all() == []  # idempotent

    with httpx.Client(timeout=5) as client:
        assert client.get(f"{manager.target.url}/health").json()["status"] == "ok"
        assert client.get(f"{manager.target.url}/products").status_code == 200

    manager.stop_all()
    assert not manager.all_running
    assert not manager.is_serving_port(manager.target.port)
    assert is_port_available(manager.target.port)

    assert manager.start_all() == []  # can be started again after a stop
    assert manager.all_running


def test_occupied_port_is_reported() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind((LOOPBACK_HOST, 0))
        blocker.listen(1)
        manager = LocalServerManager(blocker.getsockname()[1])
        try:
            errors = manager.start_all()
            assert len(errors) == 1 and TARGET_SERVICE_NAME in errors[0]
            assert not manager.target.is_running
            with pytest.raises(ServerStartError):
                manager.target.start()
        finally:
            manager.stop_all()


def test_stop_without_start_is_harmless() -> None:
    server = LocalServer("Idle", create_target_app(), free_port())
    server.stop()
    assert not server.is_running


def test_no_search_server_can_be_started_any_more() -> None:
    """The class has one server now; there is nothing to configure or ask about."""
    assert not hasattr(LocalServerManager, "configure_search")
    manager = LocalServerManager(free_port())
    assert not hasattr(manager, "search")
    assert [status.name for status in manager.statuses()] == [TARGET_SERVICE_NAME]


def test_health_check_accepts_ports_served_by_the_application(container: AppContainer) -> None:
    errors = container.servers.start_all()
    try:
        assert errors == []
        report = container.health_service.run()
        ports = [result for result in report.results if "port" in result.name.lower()]
        assert [result.name for result in ports] == ["Sample site port"]
        assert all(result.status is HealthStatus.OK for result in ports)
        assert "served by this application" in ports[0].detail
    finally:
        container.servers.stop_all()


def test_a_journey_against_a_real_domain_binds_no_local_port(
    search_container: AppContainer,
) -> None:
    """The gate is the page the visit goes to, so a remote site needs no server of ours."""
    container = search_container
    site = container.target_sites.create(
        {
            "display_name": "Remote shop",
            "base_url": "https://shop.example/",
            "environment": "CONTROLLED_TEST",
            "approved_for_automation": True,
        }
    )
    scenario = container.scenarios.search()[0]
    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}

    assert not container.servers.all_running
    container.scenarios.update(scenario.id, data | {"target_site_id": site.id})
    plan = container.runs.build_plans([scenario.id])[0]
    # A site on a real domain searches first, and a local one is visited directly -
    # and neither answer depends on a server of ours being up.
    assert plan.mode == MODE_BROWSER
    local = container.target_sites.create(
        {
            "display_name": "Local shop",
            "base_url": "http://127.0.0.1:8000/",
            "environment": Environment.LOCAL.value,
            "approved_for_automation": True,
        }
    )
    container.scenarios.update(scenario.id, data | {"target_site_id": local.id})
    assert container.runs.build_plans([scenario.id])[0].mode == MODE_DIRECT
    assert not container.servers.all_running
