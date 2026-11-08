"""Search simulator, sample target site and the server manager."""

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
from src.domain.exceptions import SafetyViolationError
from src.local_servers.common import SECURITY_HEADERS
from src.local_servers.search_simulator import selectors
from src.local_servers.search_simulator.app import create_search_app
from src.local_servers.search_simulator.dataset import (
    RESULTS_PER_PAGE,
    SearchResult,
    generate_results,
    normalise_query,
)
from src.local_servers.search_simulator.state import SimulatorConfig, SimulatorState
from src.local_servers.server_manager import LocalServer, LocalServerManager, ServerStartError
from src.local_servers.target_site import content
from src.local_servers.target_site.app import (
    EXTERNAL_REDIRECT_PATH,
    EXTERNAL_TEST_URL,
    PAGE_PATHS,
    create_target_app,
)

TARGET_URL = "http://127.0.0.1:8766/"
RESULT_ITEM = re.compile(
    rf'<li class="{selectors.RESULT_CLASS}" {selectors.RESULT_POSITION_ATTRIBUTE}="(\d+)">'
    rf'.*?<a class="{selectors.RESULT_LINK_CLASS}" href="([^"]+)"',
    re.DOTALL,
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


def only_local_target(url: str) -> None:
    if not url.startswith("http://127.0.0.1:8766"):
        raise SafetyViolationError("not an approved target site")


@pytest.fixture
def state() -> SimulatorState:
    return SimulatorState(SimulatorConfig(target_url=TARGET_URL, position=3))


@pytest.fixture
def search(state: SimulatorState) -> TestClient:
    return TestClient(create_search_app(state, only_local_target), follow_redirects=False)


@pytest.fixture
def target() -> TestClient:
    return TestClient(create_target_app(), follow_redirects=False)


def parse_results(html: str) -> list[tuple[int, str]]:
    return [(int(position), url) for position, url in RESULT_ITEM.findall(html)]


# -- dataset -------------------------------------------------------------------- #
def test_results_are_deterministic_and_use_reserved_domains() -> None:
    first = generate_results("handmade ceramic mugs")
    assert first == generate_results("handmade ceramic mugs")
    assert first != generate_results("garden tools")
    assert [result.position for result in first] == list(range(1, RESULTS_PER_PAGE + 1))
    assert len({result.url for result in first}) == RESULTS_PER_PAGE
    for result in first:
        host = result.url.split("/")[2]
        assert host.endswith(".example"), host
        assert result.title and result.description
        assert not result.is_target


@pytest.mark.parametrize("position", range(1, RESULTS_PER_PAGE + 1))
def test_target_is_inserted_at_every_position(position: int) -> None:
    target = SearchResult(position, "Target", TARGET_URL, "desc", is_target=True)
    results = generate_results("pottery", target)
    assert len(results) == RESULTS_PER_PAGE
    assert [r.position for r in results if r.is_target] == [position]
    assert results[position - 1].url == TARGET_URL


def test_normalise_query() -> None:
    assert normalise_query("  gold \t ring\n ") == "gold ring"
    assert len(normalise_query("x" * 500)) == 200


# -- search simulator pages -------------------------------------------------------- #
def test_index_page_has_the_search_form(search: TestClient) -> None:
    response = search.get("/")
    assert response.status_code == 200
    html = response.text
    assert f'id="{selectors.SEARCH_FORM_ID}"' in html
    assert f'id="{selectors.SEARCH_INPUT_ID}"' in html
    assert f'name="{selectors.QUERY_PARAMETER}"' in html
    assert f'id="{selectors.SEARCH_BUTTON_ID}"' in html
    assert "not a real search engine" in html
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_results_page_lists_ten_results_with_target_at_configured_rank(
    search: TestClient,
) -> None:
    response = search.get(selectors.SEARCH_PATH, params={"q": "handmade ceramic mugs"})
    assert response.status_code == 200
    results = parse_results(response.text)
    assert [position for position, _ in results] == list(range(1, 11))
    assert results[2] == (3, TARGET_URL)
    assert sum(1 for _, url in results if url == TARGET_URL) == 1
    assert f'class="{selectors.RESULT_TITLE_CLASS}"' in response.text
    assert f'class="{selectors.RESULT_DESCRIPTION_CLASS}"' in response.text


@pytest.mark.parametrize("position", [1, 5, 10])
def test_changing_the_position_moves_the_target(
    search: TestClient, state: SimulatorState, position: int
) -> None:
    state.set(SimulatorConfig(target_url=TARGET_URL, position=position))
    results = parse_results(search.get(selectors.SEARCH_PATH, params={"q": "mugs"}).text)
    assert results[position - 1] == (position, TARGET_URL)


def test_target_can_be_left_out_of_the_results(search: TestClient, state: SimulatorState) -> None:
    state.set(SimulatorConfig(target_url=TARGET_URL, position=None))
    results = parse_results(search.get(selectors.SEARCH_PATH, params={"q": "mugs"}).text)
    assert len(results) == 10
    assert all(url != TARGET_URL for _, url in results)


def test_empty_query_redirects_to_the_search_form(search: TestClient) -> None:
    for query in ("", "   "):
        response = search.get(selectors.SEARCH_PATH, params={"q": query})
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_query_is_html_escaped(search: TestClient) -> None:
    payload = '<script>alert("x")</script>'
    html = search.get(selectors.SEARCH_PATH, params={"q": payload}).text
    assert payload not in html
    assert "&lt;script&gt;" in html


def test_search_api_matches_the_page(search: TestClient) -> None:
    data = search.get(selectors.SEARCH_API_PATH, params={"q": "handmade ceramic mugs"}).json()
    assert (data["query"], data["count"]) == ("handmade ceramic mugs", 10)
    target_entries = [r for r in data["results"] if r["is_target"]]
    assert [(r["position"], r["url"]) for r in target_entries] == [(3, TARGET_URL)]
    page = parse_results(
        search.get(selectors.SEARCH_PATH, params={"q": "handmade ceramic mugs"}).text
    )
    assert page == [(r["position"], r["url"]) for r in data["results"]]
    assert search.get(selectors.SEARCH_API_PATH).json()["count"] == 0


# -- simulator configuration -------------------------------------------------------- #
def test_config_api_round_trip(search: TestClient, state: SimulatorState) -> None:
    assert search.get(selectors.CONFIG_API_PATH).json()["position"] == 3
    response = search.put(
        selectors.CONFIG_API_PATH,
        json={"target_url": TARGET_URL + "products", "target_title": "Shop", "position": 7},
    )
    assert response.status_code == 200
    assert state.get().position == 7
    assert state.get().target_url == TARGET_URL + "products"

    response = search.put(
        selectors.CONFIG_API_PATH, json={"target_url": TARGET_URL, "position": None}
    )
    assert response.status_code == 200 and state.get().position is None


@pytest.mark.parametrize(
    "body",
    [
        {"target_url": "https://www.google.com/", "position": 1},
        {"target_url": "https://example.com/", "position": 1},
        {"target_url": TARGET_URL, "position": 0},
        {"target_url": TARGET_URL, "position": 11},
        {"target_url": "", "position": 1},
        {"target_url": TARGET_URL, "position": 1, "unexpected": True},
    ],
)
def test_config_api_rejects_invalid_or_unapproved_targets(
    search: TestClient, state: SimulatorState, body: dict[str, object]
) -> None:
    before = state.get()
    assert search.put(selectors.CONFIG_API_PATH, json=body).status_code == 422
    assert state.get() == before


def test_admin_page_shows_current_configuration(search: TestClient) -> None:
    html = search.get("/admin").text
    assert TARGET_URL in html
    assert '<option value="3" selected>' in html
    assert "Not listed" in html


# -- sample target site -------------------------------------------------------- #
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


# -- server manager (real sockets) ------------------------------------------------ #
@pytest.fixture
def manager() -> Iterator[LocalServerManager]:
    instance = LocalServerManager(free_port(), free_port())
    yield instance
    instance.stop_all()


def test_manager_serves_both_sites_over_http_and_stops_cleanly(
    manager: LocalServerManager,
) -> None:
    assert manager.start_all() == []
    assert manager.all_running
    assert manager.is_serving_port(manager.search.port)
    assert manager.start_all() == []  # idempotent

    with httpx.Client(timeout=5) as client:
        assert client.get(f"{manager.search.url}/health").json()["service"] == "Search Simulator"
        assert client.get(f"{manager.target.url}/products").status_code == 200
        html = client.get(f"{manager.search.url}/search", params={"q": "mugs"}).text
        assert (3, f"{manager.target.url}/") in parse_results(html)

        manager.configure_search(SimulatorConfig(target_url=f"{manager.target.url}/", position=9))
        html = client.get(f"{manager.search.url}/search", params={"q": "mugs"}).text
        assert (9, f"{manager.target.url}/") in parse_results(html)

    ports = (manager.search.port, manager.target.port)
    manager.stop_all()
    assert not manager.all_running
    assert [status.running for status in manager.statuses()] == [False, False]
    assert all(is_port_available(port) for port in ports)

    assert manager.start_all() == []  # can be started again after a stop
    assert manager.all_running


def test_occupied_port_is_reported_without_blocking_the_other_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind((LOOPBACK_HOST, 0))
        blocker.listen(1)
        manager = LocalServerManager(blocker.getsockname()[1], free_port())
        try:
            errors = manager.start_all()
            assert len(errors) == 1 and "Search Simulator" in errors[0]
            assert not manager.search.is_running
            assert manager.target.is_running
            with pytest.raises(ServerStartError):
                manager.search.start()
        finally:
            manager.stop_all()


def test_stop_without_start_is_harmless() -> None:
    server = LocalServer("Idle", create_target_app(), free_port())
    server.stop()
    assert not server.is_running


def test_health_check_accepts_ports_served_by_the_application(container: AppContainer) -> None:
    errors = container.servers.start_all()
    try:
        assert errors == []
        report = container.health_service.run()
        ports = {r.name: r for r in report.results if "port" in r.name.lower()}
        assert all(result.status is HealthStatus.OK for result in ports.values())
        assert "served by this application" in ports["Search simulator port"].detail
    finally:
        container.servers.stop_all()


def test_application_simulator_only_accepts_allowlisted_targets(container: AppContainer) -> None:
    errors = container.servers.start_all()
    try:
        assert errors == []
        url = container.servers.search.url + selectors.CONFIG_API_PATH
        with httpx.Client(timeout=5) as client:
            rejected = client.put(url, json={"target_url": "https://example.com/", "position": 1})
            assert rejected.status_code == 422
            assert "not an approved target site" in rejected.json()["detail"]
            accepted = client.put(
                url, json={"target_url": container.servers.target.url + "/about", "position": 2}
            )
            assert accepted.status_code == 200
    finally:
        container.servers.stop_all()
