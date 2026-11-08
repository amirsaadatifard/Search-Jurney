"""Reading the real ranking of the target site for a journey.

A journey needs one piece of search data: *where does the target site rank for
this keyword right now?* It arrives from Google, never from a local fixture, by
one of two channels:

* :class:`GooglePageRankObserver` opens the results page in the test browser and
  reads it - no key and no quota, but Google may answer with a captcha instead;
* :class:`GoogleRankObserver` asks the official Custom Search JSON API - the
  permitted route, which needs an API key and a search-engine id.

The API answer arrives *before* the browser starts, so a site that is not listed
ends the session without loading a page; the page channel has the browser open
already, since the browser is what reads the page. Both record what they saw and
let their own error fail the session - neither invents a position.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol

from src.automation.engine import AutomationEngine
from src.automation.google_search_page import (
    MAX_RESULTS,
    find_target,
    parse_results,
    search_url,
)
from src.automation.models import (
    MODE_BROWSER,
    MODE_GOOGLE,
    RankObservation,
    ResultItem,
    SessionPlan,
)
from src.database.models import ObservedResult
from src.domain.exceptions import InvalidConfigurationError
from src.logging_config import get_logger

if TYPE_CHECKING:
    from src.services.google_research_service import GoogleResearchService

log = get_logger(__name__)

BROWSER_PROVIDER_NAME = "google-browser"


class RankObserver(Protocol):
    """Reports where ``plan``'s target site ranks for ``plan``'s keyword."""

    async def observe(
        self, plan: SessionPlan, engine: AutomationEngine | None = None
    ) -> RankObservation:
        """Return the observed ranking, or raise if the source cannot answer.

        ``engine`` is only what the browser channel needs; an API observer
        ignores it, which is why a journey can be observed before a browser
        exists at all.
        """

    @property
    def provider_name(self) -> str:
        """Name shown in the timeline and stored in the session's audit snapshot."""

    @property
    def credentials_ready(self) -> bool:
        """Whether the source can be consulted at all (fail before a run, not during)."""


def result_items(results: list[ObservedResult]) -> list[ResultItem]:
    """Turn stored result rows into the plain objects the timeline reports."""
    return [
        ResultItem(position=row.position, title=row.title, url=row.url, description=row.snippet)
        for row in results
    ]


class GoogleRankObserver:
    """Asks Google's official search API where the target domain ranks.

    The provider is synchronous (``httpx``), so the query runs on a worker
    thread and the automation loop stays responsive. ``run_search`` is the same
    entry point the Google Research page uses, which means a scenario run and a
    manual check produce comparable, auditable records.
    """

    def __init__(self, research: GoogleResearchService) -> None:
        self._research = research

    @property
    def provider_name(self) -> str:
        return self._research.provider_name

    @property
    def credentials_ready(self) -> bool:
        return self._research.credential_status().complete

    async def observe(
        self, plan: SessionPlan, engine: AutomationEngine | None = None
    ) -> RankObservation:
        observation = await asyncio.to_thread(
            self._research.run_search,
            plan.keyword,
            plan.target_domain,
            plan.search_language,
            plan.search_country,
            plan.requested_results,
        )
        log.info(
            "rank_observed",
            keyword=plan.keyword,
            domain=plan.target_domain,
            position=observation.target_position,
            results=observation.result_count,
        )
        return RankObservation(
            provider=observation.provider,
            position=observation.target_position,
            result_count=observation.result_count,
            target_url=observation.target_url or "",
            observation_id=observation.id,
            results=tuple(result_items(list(observation.results))),
        )


class GooglePageRankObserver:
    """Reads the target site's position off Google's own results page.

    No key and no quota, and the rank is exactly what this browser was shown -
    which is the point of the channel, and also its weakness: Google's terms of
    service forbid automated access, so the page may answer with a captcha
    (:class:`~src.domain.exceptions.SearchError`) and the position can differ
    from one run to the next because of the search history of this profile.
    """

    @property
    def provider_name(self) -> str:
        return BROWSER_PROVIDER_NAME

    @property
    def credentials_ready(self) -> bool:
        # Nothing to configure: the browser is the credential.
        return True

    async def observe(
        self, plan: SessionPlan, engine: AutomationEngine | None = None
    ) -> RankObservation:
        if engine is None:
            raise InvalidConfigurationError(
                "The browser search channel needs the test browser to read a results page."
            )
        url = search_url(
            plan.keyword, plan.search_language, plan.search_country, plan.requested_results
        )
        read = await engine.read_search_page(url, min(plan.requested_results, MAX_RESULTS))
        results = parse_results(list(read.items))
        listed = find_target(results, plan.target_domain)
        log.info(
            "rank_observed",
            keyword=plan.keyword,
            domain=plan.target_domain,
            position=listed.position if listed else None,
            results=len(results),
            channel="browser",
        )
        return RankObservation(
            provider=self.provider_name,
            position=listed.position if listed else None,
            result_count=len(results),
            target_url=listed.url if listed else "",
            observation_id=None,  # the page is read, not stored; the snapshot keeps the ranking
            results=tuple(
                ResultItem(position=item.position, title=item.title, url=item.url)
                for item in results
            ),
        )


class RankObserverRouter:
    """Hands each plan the rank source its scenario asked for.

    Scenarios differ - one may read the page, another use the API - but the
    runner is handed a single observer, and :meth:`check` refuses a run before it
    starts rather than half-way through the first iteration.
    """

    def __init__(self, api: GoogleRankObserver | None, page: GooglePageRankObserver | None) -> None:
        self._api = api
        self._page = page

    def observer_for(self, plan: SessionPlan) -> RankObserver:
        if plan.searches_in_browser:
            if self._page is None:
                raise InvalidConfigurationError(
                    "This scenario reads Google's results page in the browser, but no browser "
                    "engine is available to the runner."
                )
            return self._page
        if self._api is None:
            raise InvalidConfigurationError(
                "No search provider is configured, so the real ranking cannot be read. "
                "Set the Google API key and search engine id under Google Research, or switch "
                "this scenario to the browser channel."
            )
        return self._api

    def check(self, plans: Iterable[SessionPlan]) -> None:
        """Fail loudly, before any browser starts, if a plan's channel is unavailable."""
        for plan in plans:
            if plan.mode not in (MODE_BROWSER, MODE_GOOGLE):
                continue  # a direct visit asks for nothing
            observer = self.observer_for(plan)
            if not observer.credentials_ready:
                raise InvalidConfigurationError(
                    "The Google API key and search-engine id are not both set, so this run "
                    "cannot read a ranking. Add them under Google Research, or switch the "
                    "scenario to the browser channel, which needs no key."
                )

    @property
    def provider_name(self) -> str:
        """The name a pre-run message can quote; each event names its own channel."""
        return self._api.provider_name if self._api is not None else BROWSER_PROVIDER_NAME

    @property
    def credentials_ready(self) -> bool:
        return self._page is not None or (self._api is not None and self._api.credentials_ready)

    async def observe(
        self, plan: SessionPlan, engine: AutomationEngine | None = None
    ) -> RankObservation:
        return await self.observer_for(plan).observe(plan, engine)
