"""Pure logic behind the browser channel: which page to open, what it means.

Nothing here touches a browser or a key. The module builds the one URL the channel
uses, works out which kind of page the browser ended up on, numbers the result
links the way a visitor counts them and finds the approved site among them - so
all of it is unit-testable offline, and a change in Google's page format is a
change to :data:`RESULT_SELECTORS` rather than to the automation.

Automated access to Google's results page is against Google's terms of service;
the user opts into it per scenario. The channel asks for one page per iteration -
no clicking through results, no scrolling, no re-querying, and nothing done to
evade the page's own protection. If Google wants a human, the session stops and
says so.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import parse_qs, quote_plus, urlsplit

from src.domain.exceptions import SearchError
from src.search_providers.matching import domain_matches, normalize_target_domain, result_domain

SEARCH_HOST: Final = "www.google.com"
CONSENT_HOST: Final = "consent.google.com"
SEARCH_PATH: Final = "/search"
CAPTCHA_PATHS: Final = frozenset({"/sorry", "/sorry/index"})

# The result blocks Google renders, most specific first. One entry per layout the
# page has used, so an old browser profile still yields a ranking.
RESULT_SELECTORS: Final = ("div#rso div.g", "div#search div.g", "div.g")
RESULT_TITLE_SELECTORS: Final = ("h3", ".LC20lb", "a")

MAX_RESULTS: Final = 100
TEXT_SAMPLE_CHARACTERS: Final = 4000
MINIMUM_QUERY_GAP_SECONDS: Final = 2.0

# The European consent screen is a question the browser has to answer once per
# profile. The channel clicks the *least* permissive button it can find - it is
# the same choice a user would make - and reads the results page afterwards.
CONSENT_BUTTON_LABELS: Final = (
    "Reject all",
    "Alle ablehnen",
    "Alles afwijzen",
    "Tout refuser",
    "Rifiuta tutto",
    "Rechazar todo",
)

# Sentences that mean "this is a wall, not an answer". Never worked around: the
# session fails and the user is told to use the API channel instead.
BLOCK_MARKERS: Final = (
    "unusual traffic",
    "not a robot",
    "our systems have detected",
    "enable javascript and cookies",
    "capacity is limited",
)


class PageKind(StrEnum):
    """The shapes the browser can find itself looking at."""

    SEARCH = "search"
    CONSENT = "consent"
    BLOCKED = "blocked"
    OTHER = "other"


class _Pacing:
    """When the process last asked Google for a page (see :func:`pace_query`)."""

    def __init__(self) -> None:
        self.next_allowed = 0.0

    async def wait(self, gap_seconds: float) -> None:
        now = time.monotonic()
        delay = max(self.next_allowed - now, 0.0)
        self.next_allowed = now + delay + gap_seconds
        if delay > 0:
            await asyncio.sleep(delay)


_pacing = _Pacing()


async def pace_query(gap_seconds: float = MINIMUM_QUERY_GAP_SECONDS) -> None:
    """Keep at least ``gap_seconds`` between two reads of Google's page.

    Courtesy, not evasion: a run of twenty scenarios would otherwise ask twenty
    times back to back, which no person does. One run is serial, so a process-wide
    clock is all the pacing needs; it never re-tries, re-queries or hides anything.
    """
    await _pacing.wait(gap_seconds)


@dataclass(frozen=True, slots=True)
class PageResult:
    """One organic result, as the page presented it."""

    position: int
    title: str
    url: str


def search_url(query: str, language: str = "", country: str = "", count: int = 10) -> str:
    """Build the single query URL of one iteration.

    ``hl`` and ``gl`` are the same two knobs the API channel sends, so a keyword
    looked up either way is configured the same way. ``num`` is a request, not a
    promise: Google shows what it decides to show on one page.
    """
    text = " ".join(query.split())
    if not text:
        raise SearchError("Enter a keyword to search for.")
    params = {"q": text, "num": str(max(1, min(count, MAX_RESULTS)))}
    if language:
        params["hl"] = language
    if country:
        params["gl"] = country
    query_string = "&".join(f"{key}={quote_plus(params[key])}" for key in sorted(params))
    return f"https://{SEARCH_HOST}{SEARCH_PATH}?{query_string}"


def is_google_host(host: str) -> bool:
    """Whether ``host`` is google.com itself or one of its subdomains (exact labels)."""
    host = host.lower().rstrip(".")
    return host == "google.com" or host.endswith(".google.com")


def classify(url: str, page_text: str = "") -> PageKind:
    """Which page the browser is on, judged from its address and its words.

    A blocked bot is answered with an interstitial on the *same* host, so the
    text is what separates a results page from a wall.
    """
    parts = urlsplit(url or "")
    # Plain http is not a page this channel could have asked for: the entry check
    # insists on https, so anything unencrypted is treated as "somewhere else".
    if parts.scheme != "https" or not parts.netloc:
        return PageKind.OTHER
    host = parts.netloc.lower().rsplit(":", 1)[0]
    path = (parts.path or "/").rstrip("/").lower()
    if host == CONSENT_HOST:
        return PageKind.CONSENT
    if host != SEARCH_HOST:
        return PageKind.OTHER
    if path in CAPTCHA_PATHS:
        return PageKind.BLOCKED
    if path == SEARCH_PATH:
        return PageKind.BLOCKED if looks_blocked(page_text) else PageKind.SEARCH
    return PageKind.OTHER


def looks_blocked(page_text: str) -> bool:
    """Whether the page's own words are Google's refusal."""
    text = " ".join(page_text.lower().split())
    return any(marker in text for marker in BLOCK_MARKERS)


def normalize_result_link(href: str) -> str:
    """The destination of a result link, unwrapped where Google wrapped it.

    Google has linked results both directly and through ``/url?q=``. The wrapper
    is unwrapped because the destination is what the journey opens and what the
    allowlist has to judge - a ``google.com`` address is never something the
    automation may browse to.
    """
    if not href:
        return ""
    try:
        parts = urlsplit(href)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if is_google_host(parts.hostname or "") and parts.path.startswith("/url"):
        target = parse_qs(parts.query).get("q", [""])
        return target[0].strip() if target and target[0].strip() else ""
    return href.strip()


def parse_results(items: list[tuple[str, str]], limit: int = MAX_RESULTS) -> list[PageResult]:
    """Number the ``(title, href)`` pairs read from the page, in page order.

    Links that are not pages, duplicates and Google's own properties are dropped
    *before* numbering, so position 1 is the first result a person reading the
    page would see - which is what a rank has to mean.
    """
    results: list[PageResult] = []
    seen: set[str] = set()
    for title, href in items:
        url = normalize_result_link(href)
        if not url or url in seen:
            continue
        host = result_domain(url)
        if not host or is_google_host(host):
            continue  # Google's own pages are the container, never a ranking entry
        seen.add(url)
        results.append(PageResult(len(results) + 1, " ".join(title.split())[:300], url))
        if len(results) >= limit:
            break
    return results


def find_target(results: list[PageResult], target_domain: str) -> PageResult | None:
    """The first result that belongs to the approved site, or ``None``.

    Subdomains count as the site, exactly as the API channel treats them, so the
    two channels agree on what "listed" means.
    """
    domain = normalize_target_domain(target_domain) if target_domain else ""
    if not domain:
        return None
    for result in results:
        if domain_matches(result_domain(result.url), domain):
            return result
    return None


def ranking(results: list[PageResult]) -> list[tuple[int, str, str]]:
    """The ``(position, domain, title)`` tuples the session snapshot records."""
    return [(result.position, result_domain(result.url), result.title) for result in results]
