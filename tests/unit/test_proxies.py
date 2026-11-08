"""Proxy schema, credential handling, connection tester and the proxy service."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from src.app.container import AppContainer
from src.automation.plan_factory import PlanFactory
from src.domain.enums import ProxyProtocol, ProxyStatus
from src.domain.exceptions import (
    EntityInUseError,
    InvalidConfigurationError,
)
from src.domain.schemas import ProxyProfileInput, validate_input
from src.networking.credentials import (
    CredentialStoreError,
    InMemoryCredentialStore,
    KeyringCredentialStore,
    proxy_secret_key,
)
from src.networking.proxy_manager import ProxyTester
from tests.helpers.fake_proxy import FakeHttpProxy, FakeSocksServer

VALID = {"name": "Lab proxy", "protocol": "HTTP", "host": "10.0.0.5", "port": 3128}


def errors_of(data: dict[str, object]) -> dict[str, str]:
    with pytest.raises(InvalidConfigurationError) as caught:
        validate_input(ProxyProfileInput, data)
    return caught.value.field_errors


# -- schema --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("proxy.example.test", "proxy.example.test"),
        ("PROXY.Test.", "proxy.test"),
        ("10.0.0.5", "10.0.0.5"),
        ("[::1]", "::1"),
        ("localhost", "localhost"),
    ],
)
def test_valid_hosts(host: str, expected: str) -> None:
    assert validate_input(ProxyProfileInput, VALID | {"host": host}).host == expected


@pytest.mark.parametrize(
    "host",
    [
        "http://proxy.test",
        "proxy.test/path",
        "user@proxy.test",
        "bad host",
        "-bad.test",
        "a..b",
        "x" * 300,
        "",
    ],
)
def test_invalid_hosts(host: str) -> None:
    assert "host" in errors_of(VALID | {"host": host})


@pytest.mark.parametrize("port", [0, -1, 65536, "abc"])
def test_invalid_ports(port: object) -> None:
    assert "port" in errors_of(VALID | {"port": port})


def test_other_fields() -> None:
    assert "name" in errors_of(VALID | {"name": "  "})
    assert "protocol" in errors_of(VALID | {"protocol": "FTP"})
    assert "password" in errors_of(VALID | {"password": "x"})  # secrets never travel in the schema
    parsed = validate_input(ProxyProfileInput, VALID | {"protocol": "SOCKS5", "port": 1080})
    assert (parsed.protocol, parsed.port, parsed.enabled) == (ProxyProtocol.SOCKS5, 1080, True)


# -- credential stores ------------------------------------------------------------------ #
def test_in_memory_store_round_trip_and_unavailable_state() -> None:
    store = InMemoryCredentialStore()
    store.set_secret("k", "v")
    assert store.get_secret("k") == "v" and store.keys() == {"k"}
    store.delete_secret("k")
    store.delete_secret("k")  # deleting twice is harmless
    assert store.get_secret("k") is None
    with pytest.raises(CredentialStoreError):
        InMemoryCredentialStore(available=False).set_secret("k", "v")


def test_the_real_os_credential_store_round_trips() -> None:
    store = KeyringCredentialStore()
    if not store.available:
        pytest.skip("no secure credential backend on this machine")
    key = "pytest-roundtrip-do-not-keep"
    try:
        store.set_secret(key, "pässwörd ✓")
        assert store.get_secret(key) == "pässwörd ✓"
    finally:
        store.delete_secret(key)
    assert store.get_secret(key) is None
    store.delete_secret(key)  # nothing left: still no error
    with pytest.raises(CredentialStoreError, match="too long"):
        store.set_secret(key, "x" * 600)


# -- connection tester ------------------------------------------------------------------- #
@pytest.fixture
def tester() -> ProxyTester:
    return ProxyTester(timeout_seconds=2)


@pytest.fixture
def http_proxy() -> Iterator[FakeHttpProxy]:
    proxy = FakeHttpProxy()
    yield proxy
    proxy.stop()


def test_http_proxy_reachable_with_latency(tester: ProxyTester, http_proxy: FakeHttpProxy) -> None:
    result = tester.test(ProxyProtocol.HTTP, "127.0.0.1", http_proxy.port)
    assert result.ok and result.status is ProxyStatus.REACHABLE
    assert result.latency_ms is not None and 0 <= result.latency_ms < 2000
    assert "HTTP" in result.detail


def test_http_proxy_with_credentials() -> None:
    proxy = FakeHttpProxy("user:s3cret")
    try:
        tester = ProxyTester(timeout_seconds=2)
        good = tester.test(ProxyProtocol.HTTP, "127.0.0.1", proxy.port, "user", "s3cret")
        bad = tester.test(ProxyProtocol.HTTP, "127.0.0.1", proxy.port, "user", "wrong")
        anonymous = tester.test(ProxyProtocol.HTTP, "127.0.0.1", proxy.port)
    finally:
        proxy.stop()
    assert good.ok
    assert not bad.ok and "rejected" in bad.detail
    assert (
        anonymous.ok and "requires" in anonymous.detail
    )  # reachable, but it will need credentials


def test_socks5_handshake(tester: ProxyTester) -> None:
    plain, secured = FakeSocksServer(), FakeSocksServer(("user", "s3cret"))
    try:
        assert tester.test(ProxyProtocol.SOCKS5, "127.0.0.1", plain.port).ok
        assert tester.test(ProxyProtocol.SOCKS5, "127.0.0.1", secured.port, "user", "s3cret").ok
        wrong = tester.test(ProxyProtocol.SOCKS5, "127.0.0.1", secured.port, "user", "nope")
        missing = tester.test(ProxyProtocol.SOCKS5, "127.0.0.1", secured.port)
    finally:
        plain.stop()
        secured.stop()
    assert not wrong.ok and "rejected" in wrong.detail
    assert not missing.ok and "requires" in missing.detail


def test_wrong_protocol_is_detected(tester: ProxyTester, http_proxy: FakeHttpProxy) -> None:
    socks_on_http = tester.test(ProxyProtocol.SOCKS5, "127.0.0.1", http_proxy.port)
    assert not socks_on_http.ok and "SOCKS5" in socks_on_http.detail
    socks = FakeSocksServer()
    try:
        http_on_socks = ProxyTester(timeout_seconds=1).test(
            ProxyProtocol.HTTP, "127.0.0.1", socks.port
        )
    finally:
        socks.stop()
    assert not http_on_socks.ok


def test_unreachable_proxies_report_why() -> None:
    # Windows needs about two seconds before it refuses a loopback connection.
    tester = ProxyTester(timeout_seconds=6)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    refused = tester.test(ProxyProtocol.HTTP, "127.0.0.1", closed_port)
    assert refused.status is ProxyStatus.UNREACHABLE and refused.latency_ms is None
    assert "refused" in refused.detail.lower()
    assert "resolved" in tester.test(ProxyProtocol.HTTP, "no-such-host.invalid", 8080).detail


def test_a_silent_proxy_times_out() -> None:
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    stop = threading.Event()
    threading.Thread(target=lambda: (silent.accept(), stop.wait(5)), daemon=True).start()
    try:
        result = ProxyTester(timeout_seconds=0.4).test(
            ProxyProtocol.HTTP, "127.0.0.1", silent.getsockname()[1]
        )
    finally:
        stop.set()
        silent.close()
    assert result.status is ProxyStatus.UNREACHABLE and "no answer" in result.detail.lower()


# -- proxy service -------------------------------------------------------------------------- #
def memory(container: AppContainer) -> InMemoryCredentialStore:
    assert isinstance(container.credentials, InMemoryCredentialStore)
    return container.credentials


def test_password_goes_to_the_credential_store_only(container: AppContainer) -> None:
    profile = container.proxies.create(VALID | {"username": "user"}, password="s3cret")
    assert profile.has_password and memory(container).keys() == {proxy_secret_key(profile.id)}
    assert container.proxies.get_password(profile.id) == "s3cret"
    # Nothing in the database row contains it.
    with container.database.session_scope() as session:
        raw = session.connection().exec_driver_sql("SELECT * FROM proxy_profiles").fetchall()
    assert "s3cret" not in str(raw)
    assert "s3cret" not in repr(container.proxies.list()[0].__dict__)


def test_update_keeps_replaces_and_clears_the_password(container: AppContainer) -> None:
    service = container.proxies
    profile = service.create(VALID | {"username": "u"}, password="one")
    service.update(profile.id, VALID | {"username": "u", "notes": "edited"})
    assert service.get_password(profile.id) == "one"  # untouched
    service.update(profile.id, VALID | {"username": "u"}, password="two")
    assert service.get_password(profile.id) == "two"
    cleared = service.update(profile.id, VALID | {"username": "u"}, clear_password=True)
    assert not cleared.has_password and service.get_password(profile.id) is None


def test_unavailable_credential_store_refuses_the_password_and_saves_nothing(
    container: AppContainer,
) -> None:
    from src.services.proxy_service import ProxyService

    service = ProxyService(container.database, InMemoryCredentialStore(available=False))
    with pytest.raises(CredentialStoreError):
        service.create(VALID | {"username": "u"}, password="secret")
    assert service.list() == []  # the transaction was rolled back
    assert (
        service.create(VALID | {"name": "No password"}).has_password is False
    )  # profiles without one still work


def test_validation_and_duplicates(container: AppContainer) -> None:
    service = container.proxies
    service.create(VALID)
    with pytest.raises(InvalidConfigurationError) as duplicate:
        service.create(VALID | {"name": "LAB PROXY"})
    assert "name" in duplicate.value.field_errors
    with pytest.raises(InvalidConfigurationError) as long_password:
        service.create(VALID | {"name": "Other"}, password="x" * 300)
    assert "password" in long_password.value.field_errors
    assert len(service.list()) == 1


def test_delete_removes_the_secret_and_protects_profiles_in_use(container: AppContainer) -> None:
    service = container.proxies
    profile = service.create(VALID | {"username": "u"}, password="pw")
    scenario = container.scenarios.search()[0]
    from src.domain.schemas import ScenarioInput

    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}
    container.scenarios.update(scenario.id, data | {"proxy_profile_id": profile.id})
    with pytest.raises(EntityInUseError, match="used by 1 scenario"):
        service.delete(profile.id)
    container.scenarios.update(scenario.id, data | {"proxy_profile_id": None})
    service.delete(profile.id)
    assert memory(container).keys() == set() and service.list() == []


def test_test_connection_stores_status_and_resets_when_the_address_changes(
    container: AppContainer, http_proxy: FakeHttpProxy
) -> None:
    service = container.proxies
    profile = service.create(VALID | {"host": "127.0.0.1", "port": http_proxy.port})
    assert profile.last_status is ProxyStatus.UNKNOWN
    result = service.test_connection(profile.id)
    stored = service.list()[0]
    assert result.ok and stored.last_status is ProxyStatus.REACHABLE
    assert stored.last_latency_ms == result.latency_ms and stored.last_checked_at is not None

    service.update(profile.id, VALID | {"host": "127.0.0.1", "port": http_proxy.port, "notes": "x"})
    assert service.list()[0].last_status is ProxyStatus.REACHABLE  # unrelated edit keeps it
    service.update(profile.id, VALID | {"host": "127.0.0.1", "port": http_proxy.port + 1})
    stale = service.list()[0]
    assert stale.last_status is ProxyStatus.UNKNOWN and stale.last_latency_ms is None


def test_test_connection_uses_the_stored_password(container: AppContainer) -> None:
    proxy = FakeHttpProxy("user:s3cret")
    try:
        profile = container.proxies.create(
            VALID | {"host": "127.0.0.1", "port": proxy.port, "username": "user"}, password="s3cret"
        )
        assert container.proxies.test_connection(profile.id).ok
        container.proxies.update(
            profile.id,
            VALID | {"host": "127.0.0.1", "port": proxy.port, "username": "user"},
            password="changed",
        )
        assert not container.proxies.test_connection(profile.id).ok
    finally:
        proxy.stop()


def test_list_search_and_enabled_filter(container: AppContainer) -> None:
    service = container.proxies
    service.create(VALID)
    other = service.create(VALID | {"name": "Staging gateway", "host": "gw.example.test"})
    service.set_enabled(other.id, False)
    assert [p.name for p in service.list("gateway")] == ["Staging gateway"]
    assert [p.name for p in service.list(enabled_only=True)] == ["Lab proxy"]
    assert [p.name for p in service.list("10.0.0")] == ["Lab proxy"]


# -- plans ------------------------------------------------------------------------------------- #
def test_plan_carries_the_password_but_the_snapshot_does_not(container: AppContainer) -> None:
    profile = container.proxies.create(VALID | {"username": "user"}, password="s3cret")
    scenario = container.scenarios.search()[0]
    from src.domain.schemas import ScenarioInput

    data = {name: getattr(scenario, name) for name in ScenarioInput.model_fields}
    container.scenarios.update(scenario.id, data | {"proxy_profile_id": profile.id})
    plan = container.runs.build_plans([scenario.id])[0]
    assert (
        plan.proxy is not None
        and plan.proxy.password == "s3cret"
        and plan.proxy.server == "http://10.0.0.5:3128"
    )
    assert "s3cret" not in str(plan.snapshot()) and "s3cret" not in repr(plan)

    container.proxies.set_enabled(profile.id, False)
    with pytest.raises(InvalidConfigurationError, match="disabled"):
        container.runs.build_plans([scenario.id])
    container.proxies.set_enabled(profile.id, True)
    memory(container).delete_secret(proxy_secret_key(profile.id))
    with pytest.raises(InvalidConfigurationError, match="missing from the credential store"):
        container.runs.build_plans([scenario.id])
    assert isinstance(PlanFactory, type)
