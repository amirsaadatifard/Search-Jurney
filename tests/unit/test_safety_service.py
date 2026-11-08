"""Domain safety rules: URL validation, search-engine block, allowlist, redirects."""

from __future__ import annotations

import pytest

from src.domain.enums import Environment
from src.domain.exceptions import SafetyViolationError
from src.services.safety_service import (
    AllowedOrigin,
    DomainSafetyService,
    HostKind,
    Reason,
    is_public_search_engine,
    normalize_url,
)

SIMULATOR = AllowedOrigin("127.0.0.1", 8765)
DNS = {
    "staging.shop.test": ["10.0.0.12"],
    "public.example.test": ["93.184.216.34"],
    "myapp.internal": ["127.0.0.1"],
    "mixed.example.test": ["127.0.0.1", "93.184.216.34"],
}


def make_service(*allowed: AllowedOrigin) -> DomainSafetyService:
    return DomainSafetyService(
        allowlist_provider=lambda: list(allowed),
        internal_origins=[SIMULATOR],
        resolver=lambda host: DNS.get(host, []),
    )


def reason_of(error: pytest.ExceptionInfo[SafetyViolationError]) -> object:
    return error.value.context["reason"]


# -- normalisation ------------------------------------------------------------- #
def test_normalize_canonicalises_host_port_and_path() -> None:
    url = normalize_url("  HTTP://LocalHost.:8766/Products?q=1#top ")
    assert (url.scheme, url.host, url.port, url.path, url.query) == (
        "http",
        "localhost",
        8766,
        "/Products",
        "q=1",
    )
    assert url.origin == "http://localhost:8766"
    assert url.url == "http://localhost:8766/Products?q=1"
    assert normalize_url("https://example.test").url == "https://example.test/"
    assert normalize_url("https://example.test:443/a").origin == "https://example.test"
    assert normalize_url("http://[::1]:8000/").origin == "http://[::1]:8000"
    assert normalize_url("http://bücher.test/").host == "xn--bcher-kva.test"


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", Reason.MALFORMED_URL),
        ("   ", Reason.MALFORMED_URL),
        ("not a url", Reason.MALFORMED_URL),
        ("http://", Reason.MALFORMED_URL),
        ("http://exa mple.test/", Reason.MALFORMED_URL),
        ("http://example.test\\@evil.test/", Reason.MALFORMED_URL),
        ("http://example.test:99999/", Reason.MALFORMED_URL),
        ("http://a..b.test/", Reason.MALFORMED_URL),
        ("x" * 2100, Reason.MALFORMED_URL),
        ("example.test/page", Reason.FORBIDDEN_SCHEME),
        ("file:///C:/Windows/system.ini", Reason.FORBIDDEN_SCHEME),
        ("javascript:alert(1)", Reason.FORBIDDEN_SCHEME),
        ("data:text/html,<h1>x</h1>", Reason.FORBIDDEN_SCHEME),
        ("ftp://example.test/", Reason.FORBIDDEN_SCHEME),
        ("chrome://settings", Reason.FORBIDDEN_SCHEME),
        ("about:blank", Reason.FORBIDDEN_SCHEME),
        ("http://user:secret@example.test/", Reason.CREDENTIALS_IN_URL),
        ("http://user@example.test/", Reason.CREDENTIALS_IN_URL),
    ],
)
def test_normalize_rejects_unsafe_urls(url: str, reason: Reason) -> None:
    with pytest.raises(SafetyViolationError) as error:
        normalize_url(url)
    assert reason_of(error) is reason


# -- public search engines -------------------------------------------------------- #
@pytest.mark.parametrize(
    "host",
    [
        "google.com",
        "www.google.com",
        "google.co.uk",
        "www.google.com.tr",
        "images.google.de",
        "bing.com",
        "www.bing.com",
        "search.yahoo.com",
        "duckduckgo.com",
        "yandex.ru",
        "baidu.com",
        "search.brave.com",
        "ask.com",
        "GOOGLE.COM.",
    ],
)
def test_public_search_engines_are_recognised(host: str) -> None:
    assert is_public_search_engine(host)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "staging.shop.test",
        "google.com.evil.test",  # belongs to evil.test, not to the search engine
        "notgoogle.test",
        "ask.staging.shop.test",
        "::1",
    ],
)
def test_other_hosts_are_not_search_engines(host: str) -> None:
    assert not is_public_search_engine(host)


def test_search_engine_is_refused_even_when_allowlisted() -> None:
    service = make_service(AllowedOrigin("www.google.com"))
    with pytest.raises(SafetyViolationError) as error:
        service.check_url("https://www.google.com/search?q=test")
    assert reason_of(error) is Reason.PUBLIC_SEARCH_ENGINE


# -- allowlist ---------------------------------------------------------------------- #
def test_default_allowlist_contains_only_the_simulator() -> None:
    service = make_service()
    assert service.is_allowed("http://127.0.0.1:8765/search?q=mugs")
    assert not service.is_allowed("http://127.0.0.1:8766/")
    assert not service.is_allowed("http://localhost:8765/")  # different host string
    assert not service.is_allowed("https://example.com/")


def test_explicit_port_entry_allows_that_port_only() -> None:
    service = make_service(AllowedOrigin("127.0.0.1", 8766))
    assert service.check_url("http://127.0.0.1:8766/products").path == "/products"
    for blocked in ("http://127.0.0.1:9000/", "http://127.0.0.1/", "http://127.0.0.2:8766/"):
        with pytest.raises(SafetyViolationError) as error:
            service.check_url(blocked)
        assert reason_of(error) is Reason.NOT_ALLOWLISTED


def test_standard_port_entry_allows_http_and_https_but_not_subdomains() -> None:
    service = make_service(AllowedOrigin("staging.shop.test"))
    assert service.is_allowed("http://staging.shop.test/")
    assert service.is_allowed("https://staging.shop.test/cart")
    assert not service.is_allowed("https://staging.shop.test:8443/")
    assert not service.is_allowed("https://www.staging.shop.test/")
    assert not service.is_allowed("https://shop.test/")
    assert not service.is_allowed("https://staging.shop.test.evil.test/")


def test_allowed_origin_from_url() -> None:
    assert AllowedOrigin.from_url(normalize_url("http://127.0.0.1:8766/")) == AllowedOrigin(
        "127.0.0.1", 8766
    )
    assert AllowedOrigin.from_url(normalize_url("https://staging.shop.test/x")) == AllowedOrigin(
        "staging.shop.test"
    )


def test_allowlist_is_read_on_every_check() -> None:
    allowed: list[AllowedOrigin] = []
    service = DomainSafetyService(allowlist_provider=lambda: allowed)
    assert not service.is_allowed("http://127.0.0.1:8766/")
    allowed.append(AllowedOrigin("127.0.0.1", 8766))
    assert service.is_allowed("http://127.0.0.1:8766/")
    allowed.clear()  # approval revoked
    assert not service.is_allowed("http://127.0.0.1:8766/")


# -- redirects ---------------------------------------------------------------------- #
def test_redirect_inside_scope_is_accepted() -> None:
    service = make_service(AllowedOrigin("127.0.0.1", 8766))
    result = service.check_redirect("http://127.0.0.1:8766/", "http://127.0.0.1:8766/about")
    assert result.path == "/about"


@pytest.mark.parametrize(
    "destination",
    ["https://example.com/landing", "https://www.google.com/", "file:///etc/passwd"],
)
def test_redirect_leaving_scope_is_blocked(destination: str) -> None:
    service = make_service(AllowedOrigin("127.0.0.1", 8766))
    with pytest.raises(SafetyViolationError) as error:
        service.check_redirect("http://127.0.0.1:8766/", destination)
    assert reason_of(error) is Reason.REDIRECT_BLOCKED
    assert error.value.context["url"] == destination


# -- host classification and target candidates ----------------------------------------- #
@pytest.mark.parametrize(
    ("host", "kind"),
    [
        ("localhost", HostKind.LOOPBACK),
        ("app.localhost", HostKind.LOOPBACK),
        ("127.0.0.1", HostKind.LOOPBACK),
        ("127.8.9.10", HostKind.LOOPBACK),
        ("::1", HostKind.LOOPBACK),
        ("192.168.1.20", HostKind.PRIVATE),
        ("10.0.0.5", HostKind.PRIVATE),
        ("8.8.8.8", HostKind.PUBLIC),
        ("myapp.internal", HostKind.LOOPBACK),
        ("staging.shop.test", HostKind.PRIVATE),
        ("public.example.test", HostKind.PUBLIC),
        ("mixed.example.test", HostKind.PUBLIC),
        ("does-not-resolve.test", HostKind.PUBLIC),
    ],
)
def test_classify_host(host: str, kind: HostKind) -> None:
    assert make_service().classify_host(host) is kind


def test_local_environment_requires_a_loopback_host() -> None:
    service = make_service()
    assert service.validate_target_candidate("http://127.0.0.1:8766", Environment.LOCAL).url == (
        "http://127.0.0.1:8766/"
    )
    assert service.validate_target_candidate("http://myapp.internal/", Environment.LOCAL)
    for url in ("http://staging.shop.test/", "http://192.168.1.20/", "https://example.com/"):
        with pytest.raises(SafetyViolationError) as error:
            service.validate_target_candidate(url, Environment.LOCAL)
        assert reason_of(error) is Reason.NOT_LOCAL


def test_staging_candidate_accepts_other_hosts_but_never_search_engines() -> None:
    service = make_service()
    assert service.validate_target_candidate("https://staging.shop.test", Environment.STAGING)
    with pytest.raises(SafetyViolationError) as error:
        service.validate_target_candidate("https://www.bing.com/", Environment.STAGING)
    assert reason_of(error) is Reason.PUBLIC_SEARCH_ENGINE
    with pytest.raises(SafetyViolationError) as error:
        service.validate_target_candidate("file:///C:/site/index.html", Environment.DEVELOPMENT)
    assert reason_of(error) is Reason.FORBIDDEN_SCHEME
