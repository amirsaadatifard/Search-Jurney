"""Reading the real ranking of the target site before a journey opens it.

The journey needs one piece of search data: *where does the target site rank for
this keyword right now?* That number arrives from a real search provider
(:mod:`src.search_providers`), never from a local fixture, and it arrives
*before* the browser starts, so a site Google does not list ends the session
without loading a single page.

The observer is deliberately tiny: it asks the provider, keeps the answer in the
database (so a session can always be audited against the exact results that were
returned) and lets the provider's exception fail the session. It never invents a
position when the API is unavailable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from src.automation.models import RankObservation, ResultItem, SessionPlan
from src.database.models import ObservedResult
from src.logging_config import get_logger

if TYPE_CHECKING:
    from src.services.google_research_service import GoogleResearchService

log = get_logger(__name__)


class RankObserver(Protocol):
    """Reports where ``plan``'s target site ranks for ``plan``'s keyword."""

    async def observe(self, plan: SessionPlan) -> RankObservation:
        """Return the observed ranking, or raise if the provider cannot answer."""

    @property
    def provider_name(self) -> str:
        """Name shown in the timeline and stored in the session's audit snapshot."""

    @property
    def credentials_ready(self) -> bool:
        """Whether the provider can be queried at all (fail before a run, not during)."""


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

    async def observe(self, plan: SessionPlan) -> RankObservation:
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
