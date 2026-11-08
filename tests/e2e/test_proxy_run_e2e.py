"""End-to-end: real Chromium routed through a real (local, fake) proxy."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from src.app.container import AppContainer
from src.automation.models import RunSummary
from src.automation.observer import RunObserver
from src.database.repositories import SessionRepository
from src.domain.enums import EventType, SessionStatus
from tests.e2e.conftest import catalogue, configure
from tests.helpers.fake_google import FakeGoogleServer
from tests.helpers.fake_proxy import FakeHttpProxy

pytestmark = pytest.mark.e2e


class Done(RunObserver):
    def __init__(self) -> None:
        self.event = threading.Event()

    def on_run_finished(self, summary: RunSummary) -> None:
        self.event.set()


def run_once(container: AppContainer) -> None:
    probe = Done()
    container.runs.start([container.scenarios.search()[0].id], probe)
    assert probe.event.wait(90) and container.runs.join(10)


def only_session(container: AppContainer):  # type: ignore[no-untyped-def]
    with container.database.session_scope() as session:
        (record,) = SessionRepository(session).search(with_details=True)
    return record


@pytest.fixture
def proxy() -> Iterator[FakeHttpProxy]:
    server = FakeHttpProxy()
    yield server
    server.stop()


def use_proxy(container: AppContainer, port: int, **extra: object) -> None:
    profile = container.proxies.create(
        {
            "name": "Lab proxy",
            "host": "127.0.0.1",
            "port": port,
            **{k: v for k, v in extra.items() if k == "username"},
        },
        password=extra.get("password"),  # type: ignore[arg-type]
    )
    configure(
        container,
        proxy_profile_id=profile.id,
        min_dwell_seconds=1,
        max_dwell_seconds=1,
        max_scroll_actions=0,
    )


def test_session_runs_through_the_proxy(
    app: AppContainer, proxy: FakeHttpProxy, fake_google: FakeGoogleServer
) -> None:
    use_proxy(app, proxy.port)
    run_once(app)
    record = only_session(app)

    assert record.status is SessionStatus.COMPLETED and record.proxy_profile_name == "Lab proxy"
    assert record.browser_info["network_fence"] is False  # honest about the weaker containment
    started = next(e for e in record.events if e.event_type is EventType.BROWSER_STARTED)
    assert "proxy mode" in started.message
    # The visit went through the proxy; the ranking query did not, because it is
    # an API call made by the application rather than a page loaded by the browser.
    seen = " ".join(proxy.requests)
    assert f":{app.servers.target.port}" in seen
    assert f":{fake_google.port}" not in seen
    assert "Lab proxy" in str(record.settings_snapshot["proxy"]["name"])


def test_credentials_are_used_but_never_recorded(app: AppContainer) -> None:
    secured = FakeHttpProxy("user:s3cret")
    try:
        use_proxy(app, secured.port, username="user", password="s3cret")
        run_once(app)
        record = only_session(app)
        assert record.status is SessionStatus.COMPLETED
        assert secured.requests  # the pages really went through the proxy
        everything = (
            str(record.settings_snapshot)
            + str(record.browser_info)
            + str([(e.message, e.data) for e in record.events])
        )
        assert "s3cret" not in everything
    finally:
        secured.stop()


def test_wrong_proxy_password_fails_the_session_cleanly(app: AppContainer) -> None:
    secured = FakeHttpProxy("user:right")
    try:
        use_proxy(app, secured.port, username="user", password="wrong")
        run_once(app)
        record = only_session(app)
        assert record.status is SessionStatus.FAILED
        assert "s3cret" not in str(record.error_message) and "wrong" not in str(
            record.error_message
        )
    finally:
        secured.stop()


def test_dead_proxy_fails_the_session_with_a_clear_error(app: AppContainer) -> None:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    use_proxy(app, dead_port)
    run_once(app)
    record = only_session(app)
    assert record.status is SessionStatus.FAILED and record.error_type in {
        "NavigationError",
        "AutomationTimeoutError",
    }


def test_redirects_leaving_the_approved_sites_are_still_detected_in_proxy_mode(
    app: AppContainer, proxy: FakeHttpProxy, fake_google: FakeGoogleServer
) -> None:
    site = app.target_sites.create(
        {
            "display_name": "Redirecting page",
            "base_url": f"{app.servers.target.url}/test/redirect-external",
            "approved_for_automation": True,
        }
    )
    fake_google.state.items = catalogue(site.base_url, position=2)
    use_proxy(app, proxy.port)
    configure(app, target_site_id=site.id)
    run_once(app)
    record = only_session(app)
    assert record.status is SessionStatus.FAILED and record.error_type == "SafetyViolationError"
    assert EventType.SAFETY_BLOCKED in [e.event_type for e in record.events]
    assert EventType.DWELL_STARTED not in [e.event_type for e in record.events]
