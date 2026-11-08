"""Enumerations shared by every layer.

All enums are ``StrEnum`` so their values are stored verbatim in the database
and serialise naturally to JSON.
"""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    """Kind of controlled environment a target site belongs to."""

    LOCAL = "LOCAL"
    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    CONTROLLED_TEST = "CONTROLLED_TEST"


class SearchProfile(StrEnum):
    """Search environment a scenario runs against.

    Only the bundled simulator exists; the enum is the extension point for
    other *controlled* search pages (e.g. a staging site's internal search).
    """

    LOCAL_SIMULATOR = "LOCAL_SIMULATOR"


class ProxyProtocol(StrEnum):
    HTTP = "HTTP"
    HTTPS = "HTTPS"
    SOCKS5 = "SOCKS5"


class ProxyStatus(StrEnum):
    """Result of the most recent connection test of a proxy profile."""

    UNKNOWN = "UNKNOWN"
    REACHABLE = "REACHABLE"
    UNREACHABLE = "UNREACHABLE"


class UserAgentMode(StrEnum):
    """How the test browser identifies itself.

    There is deliberately no option to impersonate another browser or device.
    """

    BROWSER_DEFAULT = "BROWSER_DEFAULT"
    LABELLED_TEST_AGENT = "LABELLED_TEST_AGENT"  # default UA + an explicit test marker


class SessionStatus(StrEnum):
    """Final (or current) outcome of one automation session."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

    @property
    def is_final(self) -> bool:
        return self is not SessionStatus.RUNNING


class SessionState(StrEnum):
    """States of the automation state machine."""

    IDLE = "IDLE"
    INITIALIZING = "INITIALIZING"
    STARTING_BROWSER = "STARTING_BROWSER"
    OPENING_SEARCH = "OPENING_SEARCH"
    ENTERING_KEYWORD = "ENTERING_KEYWORD"
    SEARCHING = "SEARCHING"
    ANALYZING_RESULTS = "ANALYZING_RESULTS"
    TARGET_FOUND = "TARGET_FOUND"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    OPENING_TARGET = "OPENING_TARGET"
    DWELLING = "DWELLING"
    INTERACTING = "INTERACTING"
    CAPTURING_RESULT = "CAPTURING_RESULT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class EventType(StrEnum):
    """Entries of a session's event timeline."""

    SESSION_STARTED = "SESSION_STARTED"
    SEARCH_OBSERVED = "SEARCH_OBSERVED"
    STATE_CHANGED = "STATE_CHANGED"
    BROWSER_STARTED = "BROWSER_STARTED"
    SEARCH_PAGE_OPENED = "SEARCH_PAGE_OPENED"
    KEYWORD_ENTERED = "KEYWORD_ENTERED"
    SEARCH_SUBMITTED = "SEARCH_SUBMITTED"
    RESULTS_RECEIVED = "RESULTS_RECEIVED"
    TARGET_FOUND = "TARGET_FOUND"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_OPENED = "TARGET_OPENED"
    DWELL_STARTED = "DWELL_STARTED"
    SCROLL = "SCROLL"
    PAGE_NAVIGATION = "PAGE_NAVIGATION"
    DWELL_COMPLETED = "DWELL_COMPLETED"
    SCREENSHOT_CAPTURED = "SCREENSHOT_CAPTURED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"
    ERROR = "ERROR"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_FAILED = "SESSION_FAILED"
    SESSION_STOPPED = "SESSION_STOPPED"


class ScreenshotKind(StrEnum):
    """Moment at which a screenshot was taken."""

    SEARCH_RESULTS = "SEARCH_RESULTS"
    TARGET_OPENED = "TARGET_OPENED"
    SESSION_END = "SESSION_END"
    ERROR = "ERROR"
