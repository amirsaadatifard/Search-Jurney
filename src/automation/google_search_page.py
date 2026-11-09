"""Pure logic behind the journey's search step: which page, what it means.

Nothing in this module touches a browser. It builds the two URLs the journey needs
(Google's start page and one page of results), works out which kind of page the
browser ended up on, numbers the result links the way a visitor counts them across
several pages, finds the approved site among them and reads the "next page" control
out of a page snapshot — so all of it is unit-testable offline, and a change in
Google's page format is a change to a selector tuple rather than to the automation.

The journey in this version *is* a browse: it opens google.com in Chromium, types the
keyword into Google's own box, walks the result pages and clicks the target's link.
That is against Google's terms of service, and the application does nothing about it:
no captcha solving, no proxy rotation, no fingerprint spoofing, and one keyword per
page with a fixed pause between reads. When Google answers with a wall instead of
results the session stops and says so.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit

from src.app.constants import DEFAULT_MAX_SEARCH_PAGES
from src.automation.target_matching import domain_matches, normalize_target_domain, result_domain
from src.domain.exceptions import SearchError
from src.logging_config import get_logger

log = get_logger(__name__)

SEARCH_HOST: Final = "www.google.com"
GOOGLE_HOME: Final = "https://www.google.com/"
CONSENT_HOST: Final = "consent.google.com"
SEARCH_PATH: Final = "/search"
#: The paths that mean "a page of results", in the layouts Google has used.
SEARCH_PATHS: Final = frozenset({"/search", "/web"})
#: The path a cookie wall is served on, when a site keeps it on its own host.
CONSENT_PATH: Final = "/consent"
CAPTCHA_PATHS: Final = frozenset({"/sorry", "/sorry/index"})

# The result blocks Google renders, most specific first. One entry per layout the
# page has used, so an old browser profile still yields a ranking.
RESULT_SELECTORS: Final = ("div#rso div.g", "div#search div.g", "div.g")
RESULT_TITLE_SELECTORS: Final = ("h3", ".LC20lb", "a")
#: The anchors a result block is clicked through. The click and the parsed result
#: come from the same DOM pass, so the journey never follows a URL it has not seen.
RESULT_ANCHOR_SELECTORS: Final = ("div.g a[href]", "div#search a[href]", "a[href]")

# The search box itself, most specific first. The journey types the keyword into
# this field and presses Enter; only when no field can be found does it fall back
# to opening ``/search?q=`` directly.
KEYWORD_FIELD_SELECTORS: Final = (
    'textarea[name="q"]',
    'input[name="q"]',
    "#gbqf",
    "#APfddb textarea",
)

#: The "next" control of a results page. Google has used both an id and an
#: aria-label; a page with neither has no next page, and the walk stops.
NEXT_PAGE_SELECTORS: Final = (
    "#pnnext",
    'a[aria-label="Next"]',
    'a[aria-label="Next page"]',
    "a#nav .pnnext",
    'td.n a[aria-label*="Next"]',
)

MAX_RESULTS: Final = 100
#: Google's own page size. Sending ``num`` with this value would ask for nothing.
GOOGLE_DEFAULT_PAGE_SIZE: Final = 10
TEXT_SAMPLE_CHARACTERS: Final = 4000
MINIMUM_QUERY_GAP_SECONDS: Final = 2.0

#: The most a scenario may ask for. The walk is bounded on purpose: past a few
#: pages nobody finds a site by hand either, and each extra page is another
#: request to a service that did not ask to be polled. The default lives with the
#: other settings defaults (``src.app.constants``), so the number the Settings page
#: shows and the number the walk obeys cannot drift apart.
MAX_SEARCH_PAGES_LIMIT: Final = 10

# The European consent screen is a question the browser has to answer once per
# profile. The journey clicks the *least* permissive button it can find - it is
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
# session stops and the user is told what to do about it.
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
    HOME = "home"
    CONSENT = "consent"
    BLOCKED = "blocked"
    OTHER = "other"


class _Pacing:
    """When the process last asked Google for a page (see :func:`pace_query`)."""

    def __init__(self) -> None:
        self.next_allowed = 0.0
        self.gap = MINIMUM_QUERY_GAP_SECONDS

    def set_gap(self, seconds: float | None) -> float:
        self.gap = MINIMUM_QUERY_GAP_SECONDS if seconds is None else max(seconds, 0.0)
        return self.gap

    async def wait(self, gap_seconds: float | None = None) -> None:
        gap = self.gap if gap_seconds is None else max(gap_seconds, 0.0)
        now = time.monotonic()
        delay = max(self.next_allowed - now, 0.0)
        self.next_allowed = now + delay + gap
        if delay > 0:
            log.debug("search_paced", waiting_seconds=round(delay, 2))
            await asyncio.sleep(delay)


_pacing = _Pacing()


def set_pacing_gap(seconds: float | None) -> float:
    """Override the gap between two reads of the search page; ``None`` restores it.

    A test seam and nothing else: the end-to-end tests browse a page on this
    machine, which cannot be inconvenienced by a second request. Production never
    calls this, so the two-second courtesy pause stays in force for every journey.
    """
    gap = _pacing.set_gap(seconds)
    log.debug("search_pacing_gap", seconds=gap)
    return gap


async def pace_query(gap_seconds: float | None = None) -> None:
    """Keep at least ``gap_seconds`` between two reads of Google's page.

    Courtesy, not evasion: a run of twenty scenarios would otherwise ask twenty
    times back to back, which no person does. One run is serial, so a process-wide
    clock is all the pacing needs; it never re-tries, re-queries or hides anything.
    """
    await _pacing.wait(gap_seconds)


@dataclass(frozen=True, slots=True)
class PageResult:
    """One organic result, as the page presented it.

    ``anchor_index`` is the position of the matching ``<a>`` in the flat list of
    anchors the page's result blocks contain, which is how the journey finds the
    element to click: never a URL it invents, always the node it read.
    """

    position: int
    title: str
    url: str
    anchor_index: int = -1

    @property
    def is_clickable(self) -> bool:
        return self.anchor_index >= 0


def clamp_max_pages(value: Any) -> int:
    """The page-walk limit to use for a stored setting value."""
    try:
        pages = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SEARCH_PAGES
    return max(1, min(pages, MAX_SEARCH_PAGES_LIMIT))


def _query_string(
    query: str,
    language: str,
    country: str,
    count: int,
    page: int,
) -> str:
    """The parameter string of one results page, in the names the page itself uses.

    ``hl`` and ``gl`` are Google's language and country parameters, so a keyword
    can be looked up the same way in the app and in a browser window. ``num`` is a
    request, not a promise: the page shows what it decides to show. ``page`` is a
    visitor's page number and becomes the ``start`` offset the page's own "next"
    link uses, so the typed route and the URL route agree about positions.
    """
    text = " ".join(query.split())
    if not text:
        raise SearchError("Enter a keyword to search for.")
    per_page = max(1, min(count, MAX_RESULTS))
    page_number = max(1, int(page))
    params: dict[str, str] = {"q": text}
    if per_page != GOOGLE_DEFAULT_PAGE_SIZE:
        params["num"] = str(per_page)
    if page_number > 1:
        params["start"] = str((page_number - 1) * per_page)
    if language:
        params["hl"] = language
    if country:
        params["gl"] = country
    return urlencode(sorted(params.items()), quote_via=quote_plus)


def search_url(
    query: str,
    language: str = "",
    country: str = "",
    count: int = GOOGLE_DEFAULT_PAGE_SIZE,
    page: int = 1,
) -> str:
    """The URL of one page of Google's results (see :meth:`SearchSite.results_url`)."""
    address = _query_string(query, language, country, count, page)
    return f"https://{SEARCH_HOST}{SEARCH_PATH}?{address}"


def is_google_host(host: str) -> bool:
    """Whether ``host`` is google.com itself or one of its subdomains (exact labels)."""
    host = host.lower().rstrip(".")
    return host == "google.com" or host.endswith(".google.com")


@dataclass(frozen=True, slots=True)
class SearchSite:
    """The search engine a journey browses, named by the page it starts on.

    The shipped answer is Google, and :data:`GOOGLE_SITE` is that default. A
    deployment - or a test - can point the whole search step somewhere else by
    giving another entry page, which is the same knob the safety service uses to
    decide which pages the browser may load: one configuration, one origin, and no
    URL the search step invents on its own. The paths are Google's own, because
    what makes the page readable is precisely that it is laid out like Google's.
    """

    entry: str = GOOGLE_HOME

    @property
    def origin(self) -> str:
        """``scheme://host[:port]`` of the entry page - the only place it browses."""
        parts = urlsplit(self.entry)
        return f"{parts.scheme}://{parts.netloc}"

    def results_url(
        self,
        query: str,
        language: str = "",
        country: str = "",
        count: int = 10,
        page: int = 1,
    ) -> str:
        """One page of results on this site, in the form its own links use."""
        address = _query_string(query, language, country, count, page)
        return f"{self.origin}{SEARCH_PATH}?{address}"

    def classify(self, url: str, page_text: str = "") -> PageKind:
        """Which page of *this* site the tab is showing."""
        parts = urlsplit(url or "")
        # Both schemes are recognised on purpose: which host a journey may open at all
        # is the safety service's decision (and a test may point the whole search step
        # at a plain-HTTP page on the loopback interface). This function only says what
        # kind of page an address *is*, so it must not invent a second policy.
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return PageKind.OTHER
        host = parts.netloc.lower().rsplit(":", 1)[0]
        path = (parts.path or "/").rstrip("/").lower()
        if host == CONSENT_HOST:
            return PageKind.CONSENT
        if f"{parts.scheme}://{parts.netloc.lower()}" != self.origin.lower():
            return PageKind.OTHER
        if path in CAPTCHA_PATHS or path.startswith("/sorry/"):
            return PageKind.BLOCKED
        if path == CONSENT_PATH:
            return PageKind.CONSENT
        if path in SEARCH_PATHS:
            return PageKind.BLOCKED if looks_blocked(page_text) else PageKind.SEARCH
        if path in {"", "/"}:
            return PageKind.HOME
        return PageKind.OTHER

    def is_site(self, url: str) -> bool:
        """Whether ``url`` belongs to this site (its own host or a subdomain)."""
        host = result_domain(url)
        site = urlsplit(self.origin).hostname or ""
        return bool(host) and bool(site) and (host == site or host.endswith(f".{site}"))


GOOGLE_SITE: Final = SearchSite()


def classify(url: str, page_text: str = "") -> PageKind:
    """Which page of *Google* the browser is on, judged from address and words."""
    return GOOGLE_SITE.classify(url, page_text)


def looks_blocked(page_text: str) -> bool:
    """Whether the page's own words are Google's refusal."""
    text = " ".join(page_text.lower().split())
    return any(marker in text for marker in BLOCK_MARKERS)


def normalize_result_link(href: str) -> str:
    """The destination of a result link, unwrapped where Google wrapped it.

    Google has linked results both directly and through ``/url?q=<destination>``.
    The wrapper is unwrapped because the destination is what the journey opens and
    what the allowlist has to judge - a ``google.com`` address is never something
    the automation may browse to. Only Google's own hosts are unwrapped: a site that
    happens to have a page at ``/url?q=`` is a destination, not a wrapper, and
    rewriting it would move the journey somewhere nobody read.
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


def parse_results(
    items: Sequence[Sequence[Any]],
    *,
    offset: int = 0,
    limit: int = MAX_RESULTS,
) -> list[PageResult]:
    """Number ``(title, href[, anchor_index])`` triples read from one page.

    Positions run *across* the pages a journey walks: ``offset`` is how many
    results the earlier pages yielded, so a site found third on page two is at
    position 13 for the default ten per page — which is what a person counting
    down the results would write down.

    Links that are not pages, duplicates and Google's own properties are dropped
    *before* numbering, so position 1 is the first result a visitor sees.
    """
    results: list[PageResult] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        title, href = str(item[0]), str(item[1])
        anchor_index = int(item[2]) if len(item) > 2 else -1
        url = normalize_result_link(href)
        if not url or url in seen:
            continue
        host = result_domain(url)
        if not host or is_google_host(host):
            continue  # Google's own pages are the container, never a ranking entry
        seen.add(url)
        results.append(
            PageResult(
                offset + len(results) + 1,
                " ".join(title.split())[:300],
                url,
                anchor_index if anchor_index >= 0 else index,
            )
        )
        if len(results) >= limit:
            break
    return results


def find_target(results: Sequence[PageResult], target_domain: str) -> PageResult | None:
    """The best-ranked result that belongs to the approved site, or ``None``.

    Subdomains count as the site, which is the same rule a person applies when
    they recognise their own domain in a result.
    """
    domain = normalize_target_domain(target_domain) if target_domain else ""
    if not domain:
        return None
    for result in results:
        if domain_matches(result_domain(result.url), domain):
            return result
    return None


def ranking(results: Sequence[PageResult]) -> list[tuple[int, str, str]]:
    """The ``(position, domain, title)`` tuples the session snapshot records."""
    return [(result.position, result_domain(result.url), result.title) for result in results]


def next_page_number(
    url: str, current_page: int, per_page: int = GOOGLE_DEFAULT_PAGE_SIZE
) -> int | None:
    """The page number a "next" link points at, or ``None`` if it is unreadable.

    Only used to keep numbering honest when the click on the next-page control
    lands somewhere unexpected; ``None`` means "trust the offset we already have".
    """
    try:
        start = parse_qs(urlsplit(url).query).get("start", [""])[0]
        step = int(start) // max(per_page, 1)
    except (ValueError, ZeroDivisionError):
        return None
    return current_page + 1 if step >= current_page else step + 1


def blocked_message(url: str = "") -> str:
    """What the timeline and the run report show when Google shows its robot check."""
    where = f" ({url})" if url else ""
    return (
        f"Google answered with its robot check{where} instead of a results page, so this "
        f"network is not being served search results right now. The journey stops here on "
        f"purpose: search-journey never solves captchas, rotates proxies or spoofs a browser "
        f"fingerprint. Retry later, run the scenario from a network Google does not challenge, "
        f"or lower 'Result pages to walk' to 1 so a single page is enough."
    )


def no_results_message(url: str, detail: str) -> str:
    """The message when Google answered, but not with results we can read."""
    return (
        f"Google answered but no result links could be read from '{url}' — {detail}. If the "
        f"page looks fine in the browser window, the layout changed: the selectors in "
        f"'src/automation/google_search_page.py' need a new entry, and until then every "
        f"scenario fails the same way."
    )


def page_snapshot(counts: Sequence[tuple[str, int]]) -> str:
    """One line describing what the browser is looking at, for the debug log."""
    return ", ".join(f"{name}={count}" for name, count in counts)
