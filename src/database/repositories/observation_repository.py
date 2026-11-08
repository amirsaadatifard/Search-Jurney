"""Repository for search observations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.database.models import ObservedResult, SearchObservation
from src.database.repositories.base import Repository


class ObservationRepository(Repository[SearchObservation]):
    model = SearchObservation

    def get_with_results(self, observation_id: int) -> SearchObservation | None:
        statement = (
            select(SearchObservation)
            .where(SearchObservation.id == observation_id)
            .options(selectinload(SearchObservation.results))
        )
        return self._session.scalars(statement).first()

    def latest(self, limit: int = 50) -> Sequence[SearchObservation]:
        statement = (
            select(SearchObservation)
            .order_by(SearchObservation.created_at.desc(), SearchObservation.id.desc())
            .limit(limit)
        )
        return self._session.scalars(statement).all()

    def add_results(
        self, observation: SearchObservation, results: Sequence[ObservedResult]
    ) -> None:
        for result in results:
            observation.results.append(result)
        self._session.flush()
