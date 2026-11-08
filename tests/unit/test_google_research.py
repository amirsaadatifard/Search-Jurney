"""Google search provider (against a local double), target matching and the research service."""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from src.app.container import AppContainer
from src.app.health import HealthStatus
from src.domain.enums import Environment
from src.domain.exceptions import (
    GoogleAuthenticationError,
    GoogleNetworkError,
    GoogleQuotaError,
    GoogleSearchError,
    InvalidConfigurationError,
    SafetyViolationError,
    TargetNotFoundError,
)
from src.search_providers import (
    GoogleCustomSearchProvider,
    ProviderResult,
    domain_matches,
    find_target,
    normalize_target_domain,
    result_domain,
)
from tests.helpers.fake_google import (
    VALID_CX,
    VALID_KEY,
    FakeGoogleServer,
    error_body,
    result_item,
)

TARGET = "https://www.kilnandclay.example/shop/mugs"


def catalogue(total: int = 25, target_at: int | None = 4) -> list[dict[str, str]]:
    items = [result_item(n, f"https://site{n}.example/page") for n in range(1, total + 1)]
    if target_at is not None:
        items[target_at - 1] = result_item(target_at, TARGET, "Handmade mugs | Kiln & Clay")
    return items


def provider(
    server: FakeGoogleServer, key: str | None = VALID_KEY, cx: str | None = VALID_CX
) -> GoogleCustomSearchProvider:
    return GoogleCustomSearchProvider(
        lambda: (key, cx), endpoint=server.endpoint, timeout_seconds=5
    )


# -- matching ------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("example.com", "example.com"),
        ("  HTTPS://www.Example.com/path?q=1#x ", "example.com"),
        ("www.example.com", "example.com"),
        ("blog.example.co.uk", "blog.example.co.uk"),
        ("bücher.de", "xn--bcher-kva.de"),
        ("127.0.0.1:8766", "127.0.0.1"),
        ("http://localhost:3000/a", "localhost"),
    ],
)
def test_target_domain_is_normalised(typed: str, expected: str) -> None:
    assert normalize_target_domain(typed) == expected


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "bad domain", "http://", "a..b", "-x.com", "exa mple.com", "x" * 300 + ".com"],
)
def test_invalid_target_domains(typed: str) -> None:
    with pytest.raises(InvalidConfigurationError) as caught:
        normalize_target_domain(typed)
    assert "target_domain" in caught.value.field_errors


def test_domain_matching_rules() -> None:
    assert domain_matches("example.com", "example.com")
    assert domain_matches("www.example.com", "example.com")
    assert domain_matches("blog.shop.example.com", "example.com")
    assert domain_matches("example.com", "www.example.com")
    assert not domain_matches("notexample.com", "example.com")  # a look-alike suffix is not a match
    assert not domain_matches("example.com.evil.test", "example.com")
    assert not domain_matches("", "example.com")
    assert (
        result_domain("https://WWW.Example.com:8443/x") == "example.com"
        and result_domain("not a url") == ""
    )


def test_find_target_returns_the_best_ranked_match() -> None:
    results = [
        ProviderResult(5, "late", "https://a.example.com/", "a.example.com"),
        ProviderResult(2, "best", "https://www.example.com/", "example.com"),
        ProviderResult(1, "other", "https://other.test/", "other.test"),
    ]
    assert find_target(results, "example.com") == results[1]
    assert find_target(results, "missing.test") is None
    assert find_target([], "example.com") is None


# -- provider ------------------------------------------------------------------------------ #
def test_search_pages_through_the_api_and_numbers_results(fake_google: FakeGoogleServer) -> None:
    fake_google.state.items = catalogue(25)
    results = provider(fake_google).search("handmade mugs", "en", "us", 25)
    assert [r.position for r in results] == list(range(1, 26))
    assert results[3].url == TARGET and results[3].domain == "kilnandclay.example"
    assert results[3].title.startswith("Handmade mugs") and results[0].snippet.startswith("Snippet")
    requests = fake_google.state.requests
    assert [(r["start"], r["num"]) for r in requests] == [("1", "10"), ("11", "10"), ("21", "5")]
    assert all(r["q"] == "handmade mugs" and r["hl"] == "en" and r["gl"] == "us" for r in requests)
    assert requests[0]["key"] == VALID_KEY and requests[0]["cx"] == VALID_CX


def test_requested_count_is_respected_and_capped(fake_google: FakeGoogleServer) -> None:
    fake_google.state.items = catalogue(120, target_at=None)
    assert len(provider(fake_google).search("x", count=3)) == 3
    assert len(fake_google.state.requests) == 1
    fake_google.state.requests.clear()
    assert len(provider(fake_google).search("x", count=500)) == 100  # the API never returns more
    assert len(fake_google.state.requests) == 10


def test_fewer_results_than_requested_and_no_results(fake_google: FakeGoogleServer) -> None:
    fake_google.state.items = catalogue(7)
    assert len(provider(fake_google).search("x", count=30)) == 7
    fake_google.state.items = []
    assert provider(fake_google).search("x", count=10) == []


def test_language_and_country_are_optional(fake_google: FakeGoogleServer) -> None:
    fake_google.state.items = catalogue(3, target_at=None)
    provider(fake_google).search("x", count=3)
    assert "hl" not in fake_google.state.requests[0] and "gl" not in fake_google.state.requests[0]


def test_missing_credentials_never_reach_the_network(fake_google: FakeGoogleServer) -> None:
    for key, cx in ((None, VALID_CX), (VALID_KEY, None), ("  ", ""), (None, None)):
        with pytest.raises(GoogleAuthenticationError, match="not configured"):
            provider(fake_google, key, cx).search("x")
    assert fake_google.state.requests == []


def test_empty_keyword_is_refused(fake_google: FakeGoogleServer) -> None:
    with pytest.raises(GoogleSearchError, match="keyword"):
        provider(fake_google).search("   ")
    assert fake_google.state.requests == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            429,
            error_body(
                429, "Quota exceeded for quota metric", "rateLimitExceeded", "RESOURCE_EXHAUSTED"
            ),
            GoogleQuotaError,
        ),
        (
            403,
            error_body(403, "Daily Limit Exceeded", "dailyLimitExceeded", "PERMISSION_DENIED"),
            GoogleQuotaError,
        ),
        (
            403,
            error_body(
                403,
                "Custom Search API has not been used in project",
                "accessNotConfigured",
                "PERMISSION_DENIED",
            ),
            GoogleAuthenticationError,
        ),
        (
            401,
            error_body(
                401,
                "Request had invalid authentication credentials",
                "unauthorized",
                "UNAUTHENTICATED",
            ),
            GoogleAuthenticationError,
        ),
        (
            400,
            error_body(
                400,
                "API key not valid. Please pass a valid API key.",
                "keyInvalid",
                "INVALID_ARGUMENT",
            ),
            GoogleAuthenticationError,
        ),
        (400, error_body(400, "Invalid Value", "invalid", "INVALID_ARGUMENT"), GoogleSearchError),
        (500, {"error": {"message": "Backend Error"}}, GoogleSearchError),
        (503, {}, GoogleSearchError),
    ],
)
def test_api_errors_become_specific_exceptions(
    fake_google: FakeGoogleServer, status: int, body: dict[str, Any], expected: type[Exception]
) -> None:
    fake_google.state.fail = lambda: (status, body)
    with pytest.raises(expected) as caught:
        provider(fake_google).search("x")
    assert type(caught.value) is expected  # not a more general class
    assert len(fake_google.state.requests) == 1  # no hidden retries burning quota


def test_wrong_credentials_against_the_double(fake_google: FakeGoogleServer) -> None:
    fake_google.state.items = catalogue(3, target_at=None)
    with pytest.raises(GoogleAuthenticationError, match="not valid"):
        provider(fake_google, key="wrong-key-0000000").search("x")
    with pytest.raises(GoogleSearchError, match="search engine id"):
        provider(fake_google, cx="wrong-engine").search("x")


def test_network_problems_are_reported_as_network_errors() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    refused = GoogleCustomSearchProvider(
        lambda: (VALID_KEY, VALID_CX), endpoint=f"http://127.0.0.1:{closed}/x", timeout_seconds=3
    )
    with pytest.raises(GoogleNetworkError, match="could not be reached"):
        refused.search("x")

    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    timeout = GoogleCustomSearchProvider(
        lambda: (VALID_KEY, VALID_CX), transport=httpx.MockTransport(slow)
    )
    with pytest.raises(GoogleNetworkError, match="in time"):
        timeout.search("x")


@pytest.mark.parametrize("payload", ["not json", "[1, 2]", '{"items": [{"title": "no link"}]}'])
def test_malformed_answers_are_errors_not_fake_data(payload: str) -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, text=payload))
    with pytest.raises(GoogleSearchError):
        GoogleCustomSearchProvider(lambda: (VALID_KEY, VALID_CX), transport=transport).search("x")


def test_connection_check(fake_google: FakeGoogleServer) -> None:
    fake_google.state.items = catalogue(2, target_at=None)
    good = provider(fake_google).check()
    assert good.ok and good.latency_ms is not None and "answered" in good.detail
    bad = provider(fake_google, key=None).check()
    assert not bad.ok and "not configured" in bad.detail and bad.latency_ms is None
    fake_google.state.fail = lambda: (
        429,
        error_body(429, "Quota", "rateLimitExceeded", "RESOURCE_EXHAUSTED"),
    )
    assert "quota" in provider(fake_google).check().detail.lower()


# -- credentials and service -------------------------------------------------------------------- #
def configure(container: AppContainer) -> None:
    container.google.credentials.save(VALID_KEY, VALID_CX)


def test_credentials_are_stored_safely(google_container: AppContainer) -> None:
    google = google_container.google
    assert not google.credential_status().complete
    google.credentials.save(VALID_KEY, VALID_CX)
    status = google.credential_status()
    assert (status.api_key_set, status.engine_id, status.api_key_source) == (
        True,
        VALID_CX,
        "stored",
    )
    with google_container.database.session_scope() as session:
        dump = str(
            session.connection()
            .exec_driver_sql("SELECT key, value FROM application_settings")
            .fetchall()
        )
    assert VALID_KEY not in dump and VALID_CX in dump  # the key is not in the database
    google.credentials.save("", "new-engine")  # empty key keeps the stored one
    assert (
        google.credential_status().engine_id == "new-engine"
        and google.credentials.pair()[0] == VALID_KEY
    )
    google.credentials.clear_key()
    assert not google.credential_status().api_key_set


@pytest.mark.parametrize(
    ("key", "engine", "field"),
    [
        ("short", "ok-engine", "api_key"),
        ("x" * 300, "ok-engine", "api_key"),
        (VALID_KEY, "has space", "engine_id"),
    ],
)
def test_credential_validation(
    google_container: AppContainer, key: str, engine: str, field: str
) -> None:
    with pytest.raises(InvalidConfigurationError) as caught:
        google_container.google.credentials.save(key, engine)
    assert field in caught.value.field_errors


def test_environment_variables_are_a_fallback(
    google_container: AppContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SJAT_GOOGLE_API_KEY", VALID_KEY)
    monkeypatch.setenv("SJAT_GOOGLE_ENGINE_ID", VALID_CX)
    status = google_container.google.credential_status()
    assert status.complete and status.api_key_source == "environment"


def test_run_search_stores_the_observation_and_every_result(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    configure(google_container)
    fake_google.state.items = catalogue(12, target_at=4)
    observation = google_container.google.run_search(
        "  handmade   mugs ", "https://www.KilnAndClay.example/", "en", "us", 12
    )

    assert (observation.query, observation.target_domain) == (
        "handmade mugs",
        "kilnandclay.example",
    )
    assert (observation.target_found, observation.target_position, observation.target_url) == (
        True,
        4,
        TARGET,
    )
    assert (observation.provider, observation.language, observation.country) == (
        "google-custom-search",
        "en",
        "us",
    )
    assert (observation.requested_results, observation.result_count) == (12, 12)
    assert observation.created_at is not None and observation.id is not None
    assert [r.position for r in observation.results] == list(range(1, 13))
    assert [r.position for r in observation.results if r.is_target] == [4]
    assert (
        observation.results[3].title.startswith("Handmade mugs")
        and observation.results[3].domain == "kilnandclay.example"
    )
    assert google_container.google.get_observation(observation.id).result_count == 12


def test_a_target_that_is_not_found_is_still_an_observation(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    configure(google_container)
    fake_google.state.items = catalogue(10, target_at=None)
    observation = google_container.google.run_search("pottery", "absent.example", count=10)
    assert (
        observation.target_found is False
        and observation.target_position is None
        and observation.target_url is None
    )
    assert observation.result_count == 10 and not any(r.is_target for r in observation.results)
    assert [o.id for o in google_container.google.list_observations()] == [observation.id]


def test_subdomains_count_as_the_target(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    configure(google_container)
    fake_google.state.items = [
        result_item(1, "https://other.test/"),
        result_item(2, "https://blog.shop.example.com/post"),
    ]
    observation = google_container.google.run_search("x", "example.com", count=2)
    assert (
        observation.target_position == 2
        and observation.target_url == "https://blog.shop.example.com/post"
    )


def test_provider_errors_are_raised_and_nothing_is_stored(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    google = google_container.google
    with pytest.raises(GoogleAuthenticationError):  # not configured yet
        google.run_search("x", "example.com")
    configure(google_container)
    fake_google.state.fail = lambda: (
        429,
        error_body(429, "Quota", "rateLimitExceeded", "RESOURCE_EXHAUSTED"),
    )
    with pytest.raises(GoogleQuotaError):
        google.run_search("x", "example.com")
    assert google.list_observations() == []  # no invented data, no empty rows


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"query": "   "}, "keyword"),
        ({"query": "x" * 201}, "keyword"),
        ({"target_domain": ""}, "target_domain"),
        ({"target_domain": "bad domain"}, "target_domain"),
        ({"target_domain": "www.google.com"}, "target_domain"),
        ({"target_domain": "https://www.bing.com/search"}, "target_domain"),
        ({"count": 0}, "count"),
        ({"count": 101}, "count"),
    ],
)
def test_search_input_validation(
    google_container: AppContainer,
    fake_google: FakeGoogleServer,
    kwargs: dict[str, Any],
    field: str,
) -> None:
    configure(google_container)
    arguments: dict[str, Any] = {
        "query": "mugs",
        "target_domain": "example.com",
        "count": 10,
    } | kwargs
    with pytest.raises(InvalidConfigurationError) as caught:
        google_container.google.run_search(**arguments)
    assert field in caught.value.field_errors and fake_google.state.requests == []


def test_approving_the_observed_target_site(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    google, sites = google_container.google, google_container.target_sites
    configure(google_container)
    fake_google.state.items = catalogue(6, target_at=2)
    observation = google.run_search("mugs", "kilnandclay.example", count=6)
    assert google.approved_site_for(TARGET) is None and not google.is_visit_allowed(TARGET)

    site = google.approve_target(observation.id)
    assert (site.base_url, site.environment, site.approved_for_automation) == (
        "https://www.kilnandclay.example/",
        Environment.CONTROLLED_TEST,
        True,
    )
    assert google.approved_site_for("kilnandclay.example") is not None and google.is_visit_allowed(
        TARGET
    )
    assert google.approve_target(observation.id).id == site.id  # idempotent
    assert len([s for s in sites.search() if "kilnandclay" in s.domain]) == 1


def test_plans_for_the_direct_visit(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    google = google_container.google
    configure(google_container)
    fake_google.state.items = catalogue(6, target_at=3)
    found = google.run_search("mugs", "kilnandclay.example", count=6)
    profile_id = google_container.browser_profiles.list()[0].id

    with pytest.raises(SafetyViolationError, match="not approved"):
        google.plan_for(found.id, profile_id)
    google.approve_target(found.id)

    plan = google.plan_for(found.id, profile_id)
    assert (plan.mode, plan.iterations, plan.search_url) == ("direct", 1, "")
    assert (plan.target_url, plan.target_domain, plan.observed_position) == (
        TARGET,
        "kilnandclay.example",
        3,
    )
    assert (plan.observation_id, plan.observed_result_count, plan.provider) == (
        found.id,
        6,
        "google-custom-search",
    )
    assert (
        plan.scroll_enabled
        and plan.max_scroll_actions == 5
        and plan.scenario_name == "Google Research: mugs"
    )
    assert plan.proxy is None and plan.incognito

    quick = google.plan_for(found.id, profile_id, quick=True)
    assert (
        quick.min_dwell_seconds,
        quick.max_dwell_seconds,
        quick.scroll_enabled,
        quick.max_scroll_actions,
    ) == (3, 3, False, 0)

    fake_google.state.items = catalogue(6, target_at=None)
    missing = google.run_search("mugs again", "kilnandclay.example", count=6)
    with pytest.raises(TargetNotFoundError, match="not found"):
        google.plan_for(missing.id, profile_id)
    with pytest.raises(TargetNotFoundError):
        google.approve_target(missing.id)


# -- system checks ------------------------------------------------------------------------------- #
def checks_by_name(container: AppContainer, domain: str = "", probe: bool = True) -> dict[str, Any]:
    return {c.name: c for c in container.google.system_checks(domain, probe)}


def test_system_checks_when_everything_is_ready(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    configure(google_container)
    fake_google.state.items = catalogue(2, target_at=None)
    checks = checks_by_name(google_container, "127.0.0.1")
    assert list(checks) == [
        "Internet connection",
        "Google provider credentials",
        "Google provider availability",
        "Database",
        "Playwright",
        "Chromium",
        "Target domain allowlist",
    ]
    assert all(c.status is HealthStatus.OK for c in checks.values()), {
        n: (c.status.value, c.detail) for n, c in checks.items()
    }
    assert "approved" in checks["Target domain allowlist"].detail  # the demo site is on 127.0.0.1


def test_system_checks_name_every_problem(
    google_container: AppContainer, fake_google: FakeGoogleServer
) -> None:
    checks = checks_by_name(google_container, "nobody.example")
    assert (
        checks["Google provider credentials"].status is HealthStatus.ERROR
        and "API key" in checks["Google provider credentials"].detail
    )
    assert checks["Google provider availability"].status is HealthStatus.WARNING
    assert (
        checks["Target domain allowlist"].status is HealthStatus.WARNING
        and "not approved" in checks["Target domain allowlist"].detail
    )

    configure(google_container)
    fake_google.state.fail = lambda: (
        429,
        error_body(429, "Quota", "rateLimitExceeded", "RESOURCE_EXHAUSTED"),
    )
    assert (
        "quota" in checks_by_name(google_container)["Google provider availability"].detail.lower()
    )
    assert (
        checks_by_name(google_container, probe=False)["Google provider availability"].status
        is HealthStatus.WARNING
    )
    assert (
        checks_by_name(google_container, "bad domain")["Target domain allowlist"].status
        is HealthStatus.ERROR
    )
    assert (
        checks_by_name(google_container, "")["Target domain allowlist"].status
        is HealthStatus.WARNING
    )


def test_internet_check_reports_an_unreachable_host(google_container: AppContainer) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    google_container.google._probe = ("127.0.0.1", closed)
    check = checks_by_name(google_container)["Internet connection"]
    assert check.status is HealthStatus.ERROR and "Cannot reach" in check.detail
