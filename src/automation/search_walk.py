"""Walking Google's result pages until the target turns up - then clicking it.

One journey iteration needs three things from Google: the page of results for the
scenario's keyword, the position of the approved site on it, and - if the site is
not on the first page - the following pages, up to the limit the run settings allow.
That walk lives here, in plain async code that talks to an
:class:`~src.automation.engine.AutomationEngine` and to no browser at all, so the
whole rule - when to stop, how positions are numbered across pages, what a click
needs - is unit-testable without Chromium.

The walk stops at "found": it hands the caller the exact page entry that matched -
including which anchor on which page it came from - and the caller decides whether
to click it. That split keeps the browser in one place and the ranking rules in
another. A walk Google refused to answer raises; it is never reported as
"not listed".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from src.automation.engine import AutomationEngine
from src.automation.google_search_page import (
    MAX_RESULTS,
    PageResult,
    clamp_max_pages,
    find_target,
    parse_results,
)
from src.automation.models import RankObservation, ResultItem, SessionPlan
from src.logging_config import get_logger

log = get_logger(__name__)

#: The provider name stored with every observation and shown on the timeline: the
#: ranking came from Google's own page, read by this browser, in this region.
PROVIDER_NAME: Final = "google-browser"

#: How much of the ranking a session record copies into its audit snapshot.
SNAPSHOT_RESULT_LIMIT: Final = 10

#: Called before each page is read with its number, so the session runner can put
#: the journey's state on the timeline ("typing", "reading", "page 2 of 5"). The
#: walk owns the order; the runner owns what the user is shown.
PageHook = Callable[[int], Awaitable[None]]


@dataclass(slots=True)
class _Walk:
    """What the pages read so far add up to."""

    results: list[PageResult] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    found_on_page: int = 0

    def add(self, items: Sequence[Sequence[Any]]) -> int:
        """Number this page's results after the ones already seen; return how many."""
        before = len(self.results)
        self.results += parse_results(items, offset=before, limit=MAX_RESULTS)
        return len(self.results) - before

    @property
    def offset(self) -> int:
        return len(self.results)


@dataclass(frozen=True, slots=True)
class SearchJourney:
    """What one search step learned, and what it ended up clicking.

    ``target`` is the exact page entry that matched - including the index of the
    anchor it was read from - and ``None`` when the walk ended without finding the
    site. ``landed_url`` is only set when the journey really clicked through, in
    which case the tab already shows the target and must not be navigated again.
    """

    observation: RankObservation
    target: PageResult | None = None
    pages: tuple[str, ...] = ()
    landed_url: str = ""

    @property
    def found(self) -> bool:
        return self.target is not None

    @property
    def page_url(self) -> str:
        """The results page the target was read from - where a click starts."""
        return self.pages[-1] if self.pages else ""

    @property
    def clicked(self) -> bool:
        return bool(self.landed_url)

    def snapshot(self) -> dict[str, Any]:
        """The part of the walk a session record stores for auditing."""
        return {
            "observed_provider": self.observation.provider,
            "observed_result_count": self.observation.result_count,
            "observed_pages": len(self.pages),
            "observed_results": [
                {"position": item.position, "title": item.title, "url": item.url}
                for item in self.observation.results[:SNAPSHOT_RESULT_LIMIT]
            ],
        }


class GoogleSearchWalk:
    """Reads Google's pages through an engine until the target turns up."""

    async def run(
        self, plan: SessionPlan, engine: AutomationEngine, *, on_page: PageHook | None = None
    ) -> SearchJourney:
        """Search, then walk the result pages until the target turns up.

        ``on_page`` is awaited with the number of each page before it is read, so the
        session runner can keep its state timeline in step with what the browser is
        doing. Raises :class:`~src.domain.exceptions.SearchError` when Google will
        not show a results page at all: a walk that could not be completed fails the
        session rather than reporting "not listed".
        """
        walk = _Walk()
        limit = max(1, min(plan.requested_results, MAX_RESULTS))
        max_pages = clamp_max_pages(plan.max_search_pages)
        if on_page is not None:
            await on_page(1)
        read = await engine.search(plan, limit=limit)
        page_number = 1
        target: PageResult | None = None
        while True:
            walk.pages.append(read.page_url)
            walk.add(read.items)
            target = find_target(walk.results, plan.target_domain)
            if target is not None:
                walk.found_on_page = page_number
            log.debug(
                "search_page_walked",
                page=page_number,
                read=read.page_number,
                results=len(read.items),
                total=walk.offset,
                found=target.position if target is not None else None,
                has_next=read.has_next,
            )
            if target is not None or not read.has_next or page_number >= max_pages:
                break
            page_number += 1
            if on_page is not None:
                await on_page(page_number)
            following = await engine.next_results_page(plan, read, limit=limit)
            if following is None:
                log.debug("search_walk_ends", reason="Google offered no next page")
                break
            read = following

        observation = RankObservation(
            provider=PROVIDER_NAME,
            position=target.position if target is not None else None,
            result_count=walk.offset,
            target_url=target.url if target is not None else "",
            results=tuple(
                ResultItem(position=item.position, title=item.title, url=item.url)
                for item in walk.results
            ),
            page_number=walk.found_on_page or len(walk.pages),
            pages_walked=max(len(walk.pages), 1),
        )
        log.info(
            "search_walk_finished",
            keyword=plan.keyword,
            domain=plan.target_domain,
            pages=len(walk.pages),
            results=walk.offset,
            position=target.position if target is not None else None,
        )
        return SearchJourney(observation=observation, target=target, pages=tuple(walk.pages))
