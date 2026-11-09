"""Central domain-safety guard.

Every URL the automation is about to touch goes through
:class:`DomainSafetyService`. The rules, in order:

1. The URL must be well formed, use ``http``/``https`` and carry no credentials.
2. A public search engine is never a *target site*, not even if someone managed
   to put one on the allowlist.
3. The origin must be on the allowlist: an enabled *and* approved target site.
   Search engines are refused before this check, so adding one to the allowlist
   by hand still does not let automation browse it.

The one exception is the journey's own search step: rule 2 and rule 3 do not
apply while :meth:`DomainSafetyService.search_scope_open` is granted for a URL,
and only for the pages listed in :class:`SearchScope` - Google's start page and
its result pages, nothing else. That is what lets the application type a keyword
into Google's box, walk the result pages and click a result, which is browsing a
search engine and therefore refused for every other purpose.

The service is pure logic. The allowlist arrives through a callable and DNS
through an injectable resolver, so every rule is unit-testable offline.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

from src.domain.enums import Environment
from src.domain.exceptions import SafetyViolationError
from src.logging_config import get_logger

log = get_logger(__name__)

ALLOWED_SCHEMES: Final = frozenset({"http", "https"})
DEFAULT_PORTS: Final = {"http": 80, "https": 443}
MAX_URL_LENGTH: Final = 2000

# Brand labels refused under any top-level domain (google.com, google.co.uk, ...).
SEARCH_ENGINE_LABELS: Final = frozenset(
    {
        "google",
        "bing",
        "yahoo",
        "duckduckgo",
        "yandex",
        "baidu",
        "ecosia",
        "startpage",
        "qwant",
        "naver",
        "sogou",
        "seznam",
    }
)
# Everything the search step of a journey may open, as data rather than as a rule
# spread over the code: Google's start page and its result pages, plus the assets
# those pages load. It is a fixed set of origins and paths rather than "any search
# engine" because browsing Google's own pages is a deliberate exception to the rule
# below, and this keeps it the smallest exception we can make - ``check_url`` still
# refuses every search engine, and only a URL inside this scope is ever accepted.
#
# ``paths`` is what makes paging and clicking safe: the only google.com addresses
# the journey may open are the ones Google's own page links to. A result link is
# never followed *as a google.com URL* - it is unwrapped to its destination first
# (see :func:`~src.automation.google_search_page.normalize_result_link`) and then
# judged by the ordinary allowlist rule.
#
# The values are overridable so a test can point the whole journey at a local
# fixture; production never does.
DEFAULT_SEARCH_ENTRY: Final = "https://www.google.com/"
DEFAULT_SEARCH_PAGES: Final = ("/", "/search", "/web", "/url", "/setprefs", "/sorry")
DEFAULT_SEARCH_ASSETS: Final = ("https://www.google.com", "https://consent.google.com")


@dataclass(frozen=True, slots=True)
class SearchScope:
    """The pages the search step may open, and what those pages may load."""

    entry: str = DEFAULT_SEARCH_ENTRY
    pages: tuple[str, ...] = DEFAULT_SEARCH_PAGES
    assets: tuple[str, ...] = DEFAULT_SEARCH_ASSETS


# Engines whose name is too generic to match as a bare label.
SEARCH_ENGINE_DOMAINS: Final = frozenset(
    {"ask.com", "aol.com", "you.com", "brave.com", "kagi.com", "perplexity.ai", "mojeek.com"}
)
_SECOND_LEVEL_LABELS: Final = frozenset({"co", "com", "org", "net", "gov", "edu", "ac"})
_UNSAFE_CHARACTERS: Final = re.compile(r"[\s\\\x00-\x1f\x7f]")

HostResolver = Callable[[str], list[str]]


class Reason(StrEnum):
    """Machine-readable cause attached to every :class:`SafetyViolationError`."""

    MALFORMED_URL = "malformed_url"
    FORBIDDEN_SCHEME = "forbidden_scheme"
    CREDENTIALS_IN_URL = "credentials_in_url"
    PUBLIC_SEARCH_ENGINE = "public_search_engine"
    NOT_ALLOWLISTED = "not_allowlisted"
    SEARCH_SCOPE = "search_scope"
    REDIRECT_BLOCKED = "redirect_blocked"
    NOT_LOCAL = "not_local"


class HostKind(StrEnum):
    LOOPBACK = "loopback"
    PRIVATE = "private"
    PUBLIC = "public"


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    """A validated URL split into the parts the safety rules compare."""

    scheme: str
    host: str
    port: int
    path: str = "/"
    query: str = ""

    @property
    def has_default_port(self) -> bool:
        return DEFAULT_PORTS[self.scheme] == self.port

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}" + ("" if self.has_default_port else f":{self.port}")

    @property
    def url(self) -> str:
        return self.origin + self.path + (f"?{self.query}" if self.query else "")


@dataclass(frozen=True, slots=True)
class AllowedOrigin:
    """One allowlist entry.

    ``port`` is ``None`` for sites on the standard web ports (80/443 are then
    both accepted so an http→https upgrade is not a violation). A site
    registered with an explicit port is allowed on that port only.
    """

    host: str
    port: int | None = None

    @classmethod
    def from_url(cls, url: NormalizedUrl) -> AllowedOrigin:
        return cls(url.host, None if url.has_default_port else url.port)

    def matches(self, url: NormalizedUrl) -> bool:
        if url.host != self.host:
            return False
        if self.port is None:
            return url.port in DEFAULT_PORTS.values()
        return url.port == self.port

    def __str__(self) -> str:
        return self.host if self.port is None else f"{self.host}:{self.port}"


def normalize_url(raw_url: str) -> NormalizedUrl:
    """Parse and canonicalise ``raw_url`` or raise :class:`SafetyViolationError`."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise SafetyViolationError("The URL is empty.", reason=Reason.MALFORMED_URL)
    candidate = raw_url.strip()
    if len(candidate) > MAX_URL_LENGTH or _UNSAFE_CHARACTERS.search(candidate):
        raise SafetyViolationError(
            "The URL contains invalid characters or is too long.", reason=Reason.MALFORMED_URL
        )
    try:
        parts = urlsplit(candidate)
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise SafetyViolationError(
            f"The URL is not valid: {exc}", reason=Reason.MALFORMED_URL
        ) from exc

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        shown = scheme or "(none)"
        raise SafetyViolationError(
            f"Protocol '{shown}' is not allowed; only http and https are supported.",
            reason=Reason.FORBIDDEN_SCHEME,
        )
    if parts.username is not None or parts.password is not None:
        raise SafetyViolationError(
            "URLs must not contain a user name or password.", reason=Reason.CREDENTIALS_IN_URL
        )
    if not hostname:
        raise SafetyViolationError("The URL has no host name.", reason=Reason.MALFORMED_URL)

    return NormalizedUrl(
        scheme=scheme,
        host=_normalize_host(hostname),
        port=port if port is not None else DEFAULT_PORTS[scheme],
        path=parts.path or "/",
        query=parts.query,
    )


def _normalize_host(hostname: str) -> str:
    host = hostname.strip().rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SafetyViolationError(
            "The host name is not valid.", reason=Reason.MALFORMED_URL
        ) from exc
    if not host or ".." in host:
        raise SafetyViolationError("The host name is not valid.", reason=Reason.MALFORMED_URL)
    return host


def is_public_search_engine(host: str) -> bool:
    """Return ``True`` if ``host`` belongs to a known public search engine."""
    host = host.lower().rstrip(".")
    if _is_ip_literal(host):
        return False
    if any(host == domain or host.endswith(f".{domain}") for domain in SEARCH_ENGINE_DOMAINS):
        return True
    return _brand_label(host) in SEARCH_ENGINE_LABELS


def _brand_label(host: str) -> str:
    """Return the registrable label: ``www.google.co.uk`` → ``google``."""
    labels = host.split(".")
    if len(labels) < 2:
        return labels[0]
    labels = labels[:-1]
    if len(labels) >= 2 and labels[-1] in _SECOND_LEVEL_LABELS:
        labels = labels[:-1]
    return labels[-1]


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def classify_address(address: str) -> HostKind:
    """Classify an IP literal as loopback, private or public."""
    ip = ipaddress.ip_address(address)
    if ip.is_loopback:
        return HostKind.LOOPBACK
    if ip.is_private or ip.is_link_local:
        return HostKind.PRIVATE
    return HostKind.PUBLIC


def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` with the operating system; unresolvable names yield ``[]``."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({str(info[4][0]) for info in infos})


class DomainSafetyService:
    """Decides whether automation may touch a URL."""

    def __init__(
        self,
        allowlist_provider: Callable[[], Iterable[AllowedOrigin]],
        resolver: HostResolver = system_resolver,
        search_scope: SearchScope | None = None,
    ) -> None:
        self._allowlist_provider = allowlist_provider
        self._resolver = resolver
        scope = search_scope or SearchScope()
        self._search_scope = scope
        self._search_entry = normalize_url(scope.entry)
        self._search_pages = tuple(
            page if page.startswith("/") else f"/{page}" for page in scope.pages
        )
        self._search_asset_origins = frozenset(
            normalize_url(url).origin for url in scope.assets
        ) | {self._search_entry.origin}

    # -- queries ---------------------------------------------------------------- #
    @property
    def search_scope(self) -> SearchScope:
        """The pages the search step may browse, as configured.

        The automation reads the entry URL from here rather than naming a search
        engine itself: one configuration says where a journey searches, and both
        the guard that permits it and the browser that performs it agree on it.
        """
        return self._search_scope

    def allowed_origins(self) -> tuple[AllowedOrigin, ...]:
        """The approved target sites, read again on every call.

        There is no exempt origin: not the search provider (which is an API, not a
        page) and not the bundled sample website, which is on this list only
        because the user approved it like any other site.
        """
        return tuple(self._allowlist_provider())

    def classify_host(self, host: str) -> HostKind:
        """Classify a host name or IP literal, resolving names when necessary.

        A name counts as loopback/private only if *every* resolved address is;
        names that do not resolve are treated as public (the cautious choice).
        """
        if host == "localhost" or host.endswith(".localhost"):
            return HostKind.LOOPBACK
        if _is_ip_literal(host):
            return classify_address(host)
        kinds = {classify_address(address) for address in self._resolver(host)}
        if kinds == {HostKind.LOOPBACK}:
            return HostKind.LOOPBACK
        if kinds and HostKind.PUBLIC not in kinds:
            return HostKind.PRIVATE
        return HostKind.PUBLIC

    def is_allowed(self, url: str) -> bool:
        """Non-raising variant of :meth:`check_url`."""
        try:
            self.check_url(url)
        except SafetyViolationError:
            return False
        return True

    # -- enforcement ---------------------------------------------------------------- #
    def check_url(self, url: str) -> NormalizedUrl:
        """Return the normalised URL if automation may open it, else raise."""
        normalized = normalize_url(url)
        self._refuse_search_engine(normalized)
        if not any(origin.matches(normalized) for origin in self.allowed_origins()):
            log.warning("safety_blocked", url=normalized.origin, reason=Reason.NOT_ALLOWLISTED)
            raise SafetyViolationError(
                f"'{normalized.origin}' is not an approved target site.",
                reason=Reason.NOT_ALLOWLISTED,
                url=normalized.url,
            )
        return normalized

    def check_search_url(self, url: str) -> NormalizedUrl:
        """Return the normalised URL of a page the search step may open, or raise.

        The one place a search engine may be browsed, and only for the addresses in
        :class:`SearchScope`: the same origin as the scope's entry and one of its
        search paths. Everything else a session does - the click-through to the
        target, the dwell, the interactions - goes through :meth:`check_url`, which
        refuses search engines outright.
        """
        normalized = normalize_url(url)
        entry = self._search_entry
        entry_path = entry.path.rstrip("/") or "/"
        if normalized.origin != entry.origin:
            log.warning("search_scope_refused", url=normalized.origin + normalized.path)
            raise SafetyViolationError(
                "The search step may only open Google's own pages "
                f"('{entry.origin}'), not '{normalized.origin}'.",
                reason=Reason.SEARCH_SCOPE,
                url=normalized.url,
            )
        path = normalized.path.rstrip("/") or "/"
        if path not in self._search_pages:
            log.warning("search_scope_refused", url=normalized.origin + normalized.path)
            raise SafetyViolationError(
                f"'{normalized.path}' is not a page the search step may open; it is limited to "
                f"{', '.join(sorted(self._search_pages))}.",
                reason=Reason.SEARCH_SCOPE,
                url=normalized.url,
            )
        if path != entry_path and not normalized.query:
            # Every page in the scope except the start page is one Google answers to a
            # query: ``/search`` on its own is the home page under another spelling, and
            # a ``/url`` with nothing to redirect to is nothing at all. Requiring the
            # query is what keeps this a list of *addresses the application built* rather
            # than a licence to browse google.com.
            raise SafetyViolationError(
                f"'{path}' is only in scope with the query Google built for it.",
                reason=Reason.SEARCH_SCOPE,
                url=normalized.url,
            )
        return normalized

    def is_search_page(self, url: str) -> bool:
        """Non-raising variant of :meth:`check_search_url`."""
        try:
            self.check_search_url(url)
        except SafetyViolationError:
            return False
        return True

    def is_search_origin(self, url: str) -> bool:
        """Whether ``url`` is a page of the search scope or an asset those pages load.

        Used for two things only: letting Google's pages render and be clicked, and
        refusing to photograph them. Nothing else treats this as permission to
        browse, and it says nothing about a URL outside the search step.
        """
        try:
            normalized = normalize_url(url)
        except SafetyViolationError:
            return False
        # Any path on one of those origins, including ``/x.css``: a results page keeps
        # its stylesheets and scripts beside it. The path list belongs to the
        # navigation rule above - a request for an asset is not a navigation, and the
        # journey reads nothing from these URLs anyway.
        return normalized.origin in self._search_asset_origins

    def search_origins(self) -> tuple[AllowedOrigin, ...]:
        """The origins the network fence must add while a journey searches.

        Only the search step is widened, and only for as long as it lasts; the
        page-side request guard narrows it again to the paths in
        :class:`SearchScope`, so the fence lets Google's assets through without
        turning the browser into a general-purpose one.
        """
        origins = {self._search_entry.origin, *self._search_asset_origins}
        return tuple(AllowedOrigin.from_url(normalize_url(url)) for url in sorted(origins))

    def check_redirect(self, source_url: str, destination_url: str) -> NormalizedUrl:
        """Validate a navigation that happened *during* a session.

        Raises with :attr:`Reason.REDIRECT_BLOCKED` so the timeline shows that
        the session left the approved scope rather than started outside it.
        """
        try:
            return self.check_url(destination_url)
        except SafetyViolationError as exc:
            raise SafetyViolationError(
                f"Navigation from '{source_url}' to a non-approved address was blocked: {exc}",
                reason=Reason.REDIRECT_BLOCKED,
                cause=exc.context.get("reason"),
                url=destination_url,
            ) from exc

    def validate_target_candidate(self, base_url: str, environment: Environment) -> NormalizedUrl:
        """Check a URL the user wants to register as a target site.

        This does not consult the allowlist (the site is not on it yet); it
        rejects what may never be added: bad URLs, public search engines, and
        ``LOCAL`` sites that are not actually on this machine.
        """
        normalized = normalize_url(base_url)
        self._refuse_search_engine(normalized)
        if (
            environment is Environment.LOCAL
            and self.classify_host(normalized.host) is not HostKind.LOOPBACK
        ):
            raise SafetyViolationError(
                "A LOCAL site must use localhost or a loopback address (127.0.0.1). "
                "Choose DEVELOPMENT, STAGING or CONTROLLED_TEST for other hosts.",
                reason=Reason.NOT_LOCAL,
            )
        return normalized

    @staticmethod
    def _refuse_search_engine(url: NormalizedUrl) -> None:
        if is_public_search_engine(url.host):
            log.warning("safety_blocked", url=url.origin, reason=Reason.PUBLIC_SEARCH_ENGINE)
            raise SafetyViolationError(
                f"'{url.host}' is a public search engine. A search engine is never a target "
                "site and is never browsed for anything but the search step of a journey.",
                reason=Reason.PUBLIC_SEARCH_ENGINE,
                url=url.url,
            )
