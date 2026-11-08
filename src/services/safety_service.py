"""Central domain-safety guard.

Every URL the automation is about to touch goes through
:class:`DomainSafetyService`. The rules, in order:

1. The URL must be well formed, use ``http``/``https`` and carry no credentials.
2. Public search engines are refused unconditionally, even if someone managed
   to put one on the allowlist.
3. The origin must be on the allowlist: an enabled *and* approved target site,
   or one of the application's own local servers.

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
        internal_origins: Iterable[AllowedOrigin] = (),
        resolver: HostResolver = system_resolver,
    ) -> None:
        self._allowlist_provider = allowlist_provider
        self._internal_origins = tuple(internal_origins)
        self._resolver = resolver

    # -- queries ---------------------------------------------------------------- #
    def allowed_origins(self) -> tuple[AllowedOrigin, ...]:
        """Current allowlist: the app's own servers plus approved target sites."""
        return (*self._internal_origins, *self._allowlist_provider())

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
                f"'{url.host}' is a public search engine. This tool only works with the "
                "local search simulator and test sites you control.",
                reason=Reason.PUBLIC_SEARCH_ENGINE,
                url=url.url,
            )
