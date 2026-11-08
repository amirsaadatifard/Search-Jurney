"""Plain data objects exchanged inside the automation layer.

A :class:`SessionPlan` is an immutable copy of everything a run needs. It is
built once from the scenario, so editing the scenario while a run is in
progress cannot change that run, and it doubles as the audit snapshot stored
with every session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.domain.enums import ScreenshotKind, SessionState, SessionStatus


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    name: str
    viewport_width: int
    viewport_height: int
    headless: bool
    locale: str
    timezone: str
    downloads_enabled: bool
    javascript_enabled: bool
    clear_storage: bool
    user_agent_mode: str


@dataclass(frozen=True, slots=True)
class ProxySettings:
    """Proxy for controlled network tests. The password is never part of snapshots."""

    name: str
    protocol: str
    host: str
    port: int
    username: str = ""
    password: str | None = field(default=None, repr=False)

    @property
    def server(self) -> str:
        scheme = "socks5" if self.protocol.upper() == "SOCKS5" else self.protocol.lower()
        return f"{scheme}://{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class ScreenshotPolicy:
    search_results: bool = True
    target_opened: bool = True
    session_end: bool = True
    error: bool = True

    def allows(self, kind: ScreenshotKind) -> bool:
        return {
            ScreenshotKind.SEARCH_RESULTS: self.search_results,
            ScreenshotKind.TARGET_OPENED: self.target_opened,
            ScreenshotKind.SESSION_END: self.session_end,
            ScreenshotKind.ERROR: self.error,
        }[kind]


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """Everything needed to run one scenario (all iterations)."""

    scenario_id: int | None
    scenario_name: str
    keyword: str
    search_url: str
    target_url: str
    target_domain: str
    simulated_position: int | None
    min_dwell_seconds: int
    max_dwell_seconds: int
    iterations: int
    delay_between_iterations: int
    scroll_enabled: bool
    max_scroll_actions: int
    clear_cookies: bool
    clear_storage: bool
    incognito: bool
    browser: BrowserSettings
    proxy: ProxySettings | None = None
    screenshots: ScreenshotPolicy = ScreenshotPolicy()
    timeout_seconds: int = 30
    # "lab": search in the local simulator.  "direct": open ``target_url`` directly because a
    # real search provider already observed the target (Google Research mode).
    mode: str = "lab"
    observation_id: int | None = None
    observed_position: int | None = None
    observed_result_count: int | None = None
    provider: str = ""

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe copy for the audit trail (secrets removed)."""
        data = asdict(self)
        if data.get("proxy"):
            data["proxy"].pop("password", None)
        return data


@dataclass(frozen=True, slots=True)
class ResultItem:
    """One entry parsed from the search results page."""

    position: int
    title: str
    url: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class InteractionOutcome:
    """What a safe interaction did, ready for the event timeline."""

    event_type: str  # an EventType value
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionResult:
    """Outcome of one iteration."""

    session_id: int
    iteration: int
    status: SessionStatus
    state: SessionState
    target_found: bool | None = None
    target_position: int | None = None
    planned_dwell_seconds: float | None = None
    actual_dwell_seconds: float | None = None
    duration_seconds: float | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class RunSummary:
    """Totals over a whole run (all iterations of one or more scenarios)."""

    completed: int = 0
    target_not_found: int = 0
    failed: int = 0
    stopped: int = 0

    @property
    def total(self) -> int:
        return self.completed + self.target_not_found + self.failed + self.stopped

    def add(self, result: SessionResult) -> None:
        match result.status:
            case SessionStatus.COMPLETED:
                self.completed += 1
            case SessionStatus.TARGET_NOT_FOUND:
                self.target_not_found += 1
            case SessionStatus.STOPPED:
                self.stopped += 1
            case _:
                self.failed += 1
