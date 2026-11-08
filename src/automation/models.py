"""Plain data objects exchanged inside the automation layer.

A :class:`SessionPlan` is an immutable copy of everything a run needs. It is
built once from the scenario, so editing the scenario while a run is in
progress cannot change that run, and it doubles as the audit snapshot stored
with every session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Final

from src.app.constants import DEFAULT_SEARCH_RESULTS
from src.domain.enums import ScreenshotKind, SessionState, SessionStatus

# How a journey reaches its target.
MODE_BROWSER: Final = "browser"  # read Google's own results page in the browser
MODE_GOOGLE: Final = "google"  # read the real ranking first, then open the target
MODE_DIRECT: Final = "direct"  # open the target without a search step


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
    """Which moments of a journey are photographed.

    ``SEARCH_RESULTS`` belongs to the version of this project that browsed a
    local results page. A journey now reads its ranking from the search API and
    only opens the target, so no results page exists to photograph; the kind
    stays in :class:`~src.domain.enums.ScreenshotKind` because old sessions still
    reference it, and :meth:`allows` never enables it.
    """

    target_opened: bool = True
    session_end: bool = True
    error: bool = True

    def allows(self, kind: ScreenshotKind) -> bool:
        return {
            ScreenshotKind.TARGET_OPENED: self.target_opened,
            ScreenshotKind.SESSION_END: self.session_end,
            ScreenshotKind.ERROR: self.error,
        }.get(kind, False)


@dataclass(frozen=True, slots=True)
class SessionPlan:
    """Everything needed to run one scenario (all iterations)."""

    scenario_id: int | None
    scenario_name: str
    keyword: str
    target_url: str
    target_domain: str
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
    # ``MODE_BROWSER``: read Google's results page in the browser (no key).
    # ``MODE_GOOGLE``: ask the real search provider where the target ranks, then open it.
    # ``MODE_DIRECT``: open ``target_url`` directly because a search was already
    # observed (Google Research mode) or because Google cannot list this site at all.
    mode: str = MODE_GOOGLE
    observation_id: int | None = None
    observed_position: int | None = None
    observed_result_count: int | None = None
    provider: str = ""
    # Query options for the search step (both channels; ``MODE_DIRECT`` ignores them).
    search_language: str = ""
    search_country: str = ""
    requested_results: int = DEFAULT_SEARCH_RESULTS

    @property
    def searches_in_browser(self) -> bool:
        """Whether the ranking comes from Google's own page instead of from the API.

        The one channel that needs a key for nothing - and the one that Google's
        terms of service do not like, which is why it is a per-scenario choice
        rather than a default that silently applies to everyone.
        """
        return self.mode == MODE_BROWSER

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe copy for the audit trail (secrets removed)."""
        data = asdict(self)
        if data.get("proxy"):
            data["proxy"].pop("password", None)
        return data


@dataclass(frozen=True, slots=True)
class SearchPageRead:
    """What the browser found when it was asked to read a results page.

    ``kind`` is a :class:`~src.automation.google_search_page.PageKind` value:
    ``search`` carries ``items``, anything else carries an explanation. The
    items are the raw ``(title, href)`` pairs in page order; numbering them and
    dropping the non-result links is the parser's job, not the browser's.
    """

    kind: str
    items: tuple[tuple[str, str], ...] = ()
    page_url: str = ""
    text: str = ""


@dataclass(frozen=True, slots=True)
class ResultItem:
    """One entry of a search result list."""

    position: int
    title: str
    url: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class RankObservation:
    """What the real search provider reported about one keyword.

    Produced by a :class:`~src.automation.rank_observer.RankObserver` before the
    browser is started. ``position`` is ``None`` when the target was not among
    the results, which is a *finding*, not an error.
    """

    provider: str
    position: int | None = None
    result_count: int = 0
    target_url: str = ""
    observation_id: int | None = None
    results: tuple[ResultItem, ...] = ()


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
