"""Target-domain normalisation and matching against search results."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from typing import Final
from urllib.parse import urlsplit

from src.domain.exceptions import InvalidConfigurationError
from src.search_providers.base import ProviderResult

_LABEL: Final = r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
_HOST_PATTERN: Final = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_WWW: Final = "www."


def _strip_www(host: str) -> str:
    return host[len(_WWW) :] if host.startswith(_WWW) else host


def normalize_target_domain(text: str) -> str:
    """Reduce what the user typed (``https://www.Example.com/page``) to ``example.com``.

    Raises :class:`InvalidConfigurationError` if no valid host name is left.
    """
    raw = text.strip()
    if not raw:
        raise InvalidConfigurationError(
            "Enter a target domain.", field_errors={"target_domain": "Enter a target domain."}
        )
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    try:
        host = (parsed.hostname or "").strip(".").lower()
    except ValueError:
        host = ""
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        host = ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not host or len(host) > 253 or not _HOST_PATTERN.fullmatch(host):
            raise InvalidConfigurationError(
                "The target domain is not valid.",
                field_errors={"target_domain": "Use a domain such as example.com."},
            ) from None
    return _strip_www(host)


def result_domain(url: str) -> str:
    """Host of a result URL without a leading ``www.`` ('' if the URL has none)."""
    try:
        host = (urlsplit(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return _strip_www(host)


def domain_matches(result_url_domain: str, target_domain: str) -> bool:
    """``True`` if the result's domain is the target or one of its subdomains."""
    domain = _strip_www(result_url_domain.lower())
    target = _strip_www(target_domain.lower())
    return bool(domain) and (domain == target or domain.endswith(f".{target}"))


def find_target(results: Sequence[ProviderResult], target_domain: str) -> ProviderResult | None:
    """The best-ranked result on the target domain, or ``None``."""
    for result in sorted(results, key=lambda item: item.position):
        if domain_matches(result.domain, target_domain):
            return result
    return None
