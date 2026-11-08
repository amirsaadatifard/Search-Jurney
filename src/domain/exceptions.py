"""Application exception hierarchy.

Every error the application raises on purpose derives from :class:`AppError`,
so callers can distinguish expected failures (shown to the user, recorded on a
session) from programming errors (logged with a traceback).
"""

from __future__ import annotations

from collections.abc import Mapping


class AppError(Exception):
    """Base class for all expected application errors."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, object] = dict(context)

    def __str__(self) -> str:
        return self.message


# -- configuration / data ---------------------------------------------------- #
class InvalidConfigurationError(AppError):
    """User-supplied data failed validation.

    ``field_errors`` maps a field name to a human-readable message so forms can
    show each problem next to the offending input.
    """

    def __init__(self, message: str, field_errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.field_errors: dict[str, str] = dict(field_errors or {})


class EntityNotFoundError(AppError):
    """A requested record does not exist."""


class DuplicateEntityError(AppError):
    """A record with the same unique value already exists."""


class EntityInUseError(AppError):
    """A record cannot be deleted because other records depend on it."""


# -- safety -------------------------------------------------------------------- #
class SafetyViolationError(AppError):
    """Automation was asked to touch a URL outside the approved scope."""


# -- search provider (Google) ----------------------------------------------------- #
class GoogleSearchError(AppError):
    """The search provider answered with something that is not a usable result."""


class GoogleAuthenticationError(GoogleSearchError):
    """The API key or search-engine id is missing, wrong, or not allowed to search."""


class GoogleQuotaError(GoogleSearchError):
    """The daily or per-minute quota of the search API is used up."""


class GoogleNetworkError(GoogleSearchError):
    """The search provider could not be reached (no internet, DNS, timeout)."""


# -- automation ---------------------------------------------------------------- #
class AutomationError(AppError):
    """Base class for failures while executing a session."""


class BrowserLaunchError(AutomationError):
    """The browser could not be started."""


class NavigationError(AutomationError):
    """A page failed to load."""


class SearchError(AutomationError):
    """Google's results page could not be read in the browser.

    The browser channel has no fallback: the error says what Google answered
    (a consent screen it would not click, a captcha, a page it no longer
    recognises) so the user knows whether to retry later or switch the
    scenario to the API channel.
    """


class TargetNotFoundError(AutomationError):
    """The target domain is not present in the search results."""


class AutomationTimeoutError(AutomationError):
    """An automation step exceeded its timeout.

    Named differently from the built-in ``TimeoutError`` to avoid shadowing it.
    """


class ProxyConnectionError(AutomationError):
    """The configured proxy could not be reached."""


class SessionCancelledError(AutomationError):
    """The user stopped the run; not a failure of the system under test."""


class InvalidStateTransitionError(AutomationError):
    """The state machine was asked to perform a transition that is not allowed."""
