"""The bundled sample website, the search-API double and the server manager.

The sample site is the only server this application owns, so this file also
proves the thing the journeys depend on: the search data does *not* come from a
server of ours but from the provider's API - here a local double of Google's
documented response format, reached over a real socket.
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
from src.automation.models import MODE_BROWSER, MODE_GOOGLE
from src.domain.enums import SearchProfile
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
from tests.helpers.fake_google import VALID_CX, VALID_KEY, FakeGoogleServer, result_item


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


# -- the search API double ------------------------------------------------------- #
def test_the_double_answers_like_googles_api(fake_google: FakeGoogleServer) -> None:
    """The shape the provider client parses: ``items``, ``searchInformation``, errors."""
    fake_google.state.items = [result_item(1, "https://shop.example/"), result_item(2, "x")]
    with httpx.Client(timeout=5) as client:
        body = client.get(
            fake_google.endpoint, params={"key": VALID_KEY, "cx": VALID_CX, "q": "mugs", "num": 10}
        ).json()
    assert body["kind"] == "customsearch#search"
    assert [item["link"] for item in body["items"]] == ["https://shop.example/", "x"]
    assert body["searchInformation"]["totalResults"] == "2"

    with httpx.Client(timeout=5) as client:
        refused = client.get(fake_google.endpoint, params={"key": "nope", "cx": VALID_CX, "q": "x"})
    assert refused.status_code == 400
    assert refused.json()["error"]["errors"][0]["reason"] == "keyInvalid"


def test_the_application_reads_the_ranking_through_the_double(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    """A real HTTP round trip, the real service, the real query - and no browser."""
    google = google_container.google
    google.credentials.save(VALID_KEY, VALID_CX)
    fake_google.state.items = [
        result_item(1, "https://other1.example/page"),
        result_item(2, "https://other2.example/page"),
        result_item(3, "https://shop.example/products"),
    ]
    site = google_container.target_sites.create(
        {
            "display_name": "Listed shop",
            "base_url": "https://shop.example/",
            "environment": "CONTROLLED_TEST",
            "approved_for_automation": True,
        }
    )

    observation = google.run_search("handmade mugs", site.domain, count=10)

    assert (observation.target_position, observation.result_count) == (3, 3)
    assert observation.results[2].url == "https://shop.example/products"
    assert [call["q"] for call in fake_google.state.requests] == ["handmade mugs"]
    assert fake_google.state.requests[0]["cx"] == VALID_CX
    assert observation.target_url == "https://shop.example/products"


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
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    """The gate is the page the visit goes to, so a remote site needs no server of ours."""
    container = google_container
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
    fake_google.state.items = [result_item(1, "https://shop.example/")]

    assert not container.servers.all_running
    # Either way of reading the ranking asks Google, not a server of ours.
    for profile, mode in (
        (SearchProfile.GOOGLE_API, MODE_GOOGLE),
        (SearchProfile.GOOGLE_BROWSER, MODE_BROWSER),
    ):
        container.scenarios.update(
            scenario.id, data | {"target_site_id": site.id, "search_profile": profile}
        )
        plan = container.runs.build_plans([scenario.id])[0]
        assert plan.mode == mode and not container.servers.all_running
