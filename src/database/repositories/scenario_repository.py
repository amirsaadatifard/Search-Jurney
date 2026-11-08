"""Repository for :class:`Scenario`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy import func, select

from src.database.models.scenario import Scenario
from src.database.models.target_site import TargetSite
from src.database.repositories.base import Repository, like_pattern

# Whitelist of sortable columns: user input never reaches ORDER BY directly.
SCENARIO_SORT_COLUMNS: Final[dict[str, Any]] = {
    "name": func.lower(Scenario.name),
    "keyword": func.lower(Scenario.keyword),
    "iterations": Scenario.iterations,
    "enabled": Scenario.enabled,
    "created_at": Scenario.created_at,
    "updated_at": Scenario.updated_at,
}
DEFAULT_SCENARIO_SORT: Final = "name"


class ScenarioRepository(Repository[Scenario]):
    model = Scenario

    def get_by_name(self, name: str) -> Scenario | None:
        statement = select(Scenario).where(func.lower(Scenario.name) == name.strip().lower())
        return self._session.scalars(statement).unique().first()

    def search(
        self,
        text: str = "",
        enabled: bool | None = None,
        target_site_id: int | None = None,
        sort_by: str = DEFAULT_SCENARIO_SORT,
        descending: bool = False,
    ) -> Sequence[Scenario]:
        """Filter and sort scenarios. Unknown ``sort_by`` values fall back to name."""
        statement = select(Scenario)
        if text.strip():
            pattern = like_pattern(text.strip())
            statement = statement.join(Scenario.target_site).where(
                Scenario.name.ilike(pattern, escape="\\")
                | Scenario.keyword.ilike(pattern, escape="\\")
                | Scenario.description.ilike(pattern, escape="\\")
                | TargetSite.display_name.ilike(pattern, escape="\\")
            )
        if enabled is not None:
            statement = statement.where(Scenario.enabled.is_(enabled))
        if target_site_id is not None:
            statement = statement.where(Scenario.target_site_id == target_site_id)

        column = SCENARIO_SORT_COLUMNS.get(sort_by, SCENARIO_SORT_COLUMNS[DEFAULT_SCENARIO_SORT])
        statement = statement.order_by(column.desc() if descending else column.asc(), Scenario.id)
        return self._session.scalars(statement).unique().all()

    def count_enabled(self) -> int:
        statement = select(func.count()).select_from(Scenario).where(Scenario.enabled.is_(True))
        return self._session.scalar(statement) or 0

    def count_using_target_site(self, target_site_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Scenario)
            .where(Scenario.target_site_id == target_site_id)
        )
        return self._session.scalar(statement) or 0

    def count_using_proxy_profile(self, proxy_profile_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Scenario)
            .where(Scenario.proxy_profile_id == proxy_profile_id)
        )
        return self._session.scalar(statement) or 0

    def count_using_browser_profile(self, browser_profile_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(Scenario)
            .where(Scenario.browser_profile_id == browser_profile_id)
        )
        return self._session.scalar(statement) or 0
