"""Recording what Google actually showed a journey.

A session row says where the target ranked; the observation says what the search
engine returned at that moment, which is the difference between "the app claims
position 3" and "position 3 is what this browser was shown, on this date, from
this region". One observation is written per search step of an iteration, from the
pages the journey actually read, and the session links to it.

Nothing here invents a ranking: a row exists only after Google answered, and the
results stored are the ones the page contained.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.automation.models import SessionPlan
from src.automation.search_walk import SearchJourney
from src.automation.target_matching import result_domain
from src.database.models import ObservedResult, SearchObservation
from src.database.repositories.observation_repository import ObservationRepository
from src.database.session import Database
from src.logging_config import get_logger

log = get_logger(__name__)


class SearchJournal:
    """Writes one observation per search step, in its own short transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def record(self, plan: SessionPlan, journey: SearchJourney) -> int | None:
        """Store what the walk found; return the new row's id, or ``None``.

        A journal that cannot write must not end a journey - the session record
        already holds the same numbers in its audit snapshot - so a failure here is
        reported as a warning instead of being raised. That is the only place in the
        search step where an error does not reach the run.
        """
        try:
            with self._database.session_scope() as session:
                return self._add(session, plan, journey)
        except Exception as exc:  # persistence must never break a run
            log.warning("search_journal_failed", error=str(exc), keyword=plan.keyword)
            return None

    def _add(self, session: Session, plan: SessionPlan, journey: SearchJourney) -> int:
        repository = ObservationRepository(session)
        observation = repository.add(
            SearchObservation(
                query=plan.keyword[:200],
                target_domain=plan.target_domain[:255],
                target_found=journey.found,
                target_position=journey.observation.position,
                target_url=journey.target.url if journey.target is not None else None,
                provider=journey.observation.provider,
                language=plan.search_language,
                country=plan.search_country,
                requested_results=plan.requested_results,
                result_count=journey.observation.result_count,
            )
        )
        repository.add_results(observation, self._results(plan, journey))
        log.debug(
            "search_observation_recorded",
            observation_id=observation.id,
            position=journey.observation.position,
            results=journey.observation.result_count,
        )
        return int(observation.id)

    @staticmethod
    def _results(plan: SessionPlan, journey: SearchJourney) -> list[ObservedResult]:
        target_url = journey.target.url if journey.target is not None else ""
        return [
            ObservedResult(
                position=item.position,
                title=item.title[:500],
                domain=result_domain(item.url)[:255],
                url=item.url[:2000],
                snippet="",  # the journey reads links; a snippet is not part of that read
                is_target=bool(target_url) and item.url == target_url,
            )
            for item in journey.observation.results
        ]
