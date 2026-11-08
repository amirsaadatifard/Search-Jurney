"""Repository for :class:`TargetSite`."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from src.database.models.target_site import TargetSite
from src.database.repositories.base import Repository, like_pattern
from src.domain.enums import Environment


class TargetSiteRepository(Repository[TargetSite]):
    model = TargetSite

    def get_by_name(self, display_name: str) -> TargetSite | None:
        statement = select(TargetSite).where(
            func.lower(TargetSite.display_name) == display_name.strip().lower()
        )
        return self._session.scalars(statement).first()

    def find_by_domain(self, domain: str) -> Sequence[TargetSite]:
        statement = select(TargetSite).where(TargetSite.domain == domain.strip().lower())
        return self._session.scalars(statement).all()

    def list_automation_allowed(self) -> Sequence[TargetSite]:
        """Sites that are both enabled and approved: the effective allowlist."""
        statement = (
            select(TargetSite)
            .where(TargetSite.enabled.is_(True), TargetSite.approved_for_automation.is_(True))
            .order_by(func.lower(TargetSite.display_name))
        )
        return self._session.scalars(statement).all()

    def search(
        self,
        text: str = "",
        environment: Environment | None = None,
        approved: bool | None = None,
    ) -> Sequence[TargetSite]:
        statement = select(TargetSite).order_by(func.lower(TargetSite.display_name))
        if text.strip():
            pattern = like_pattern(text.strip())
            statement = statement.where(
                TargetSite.display_name.ilike(pattern, escape="\\")
                | TargetSite.domain.ilike(pattern, escape="\\")
                | TargetSite.base_url.ilike(pattern, escape="\\")
            )
        if environment is not None:
            statement = statement.where(TargetSite.environment == environment)
        if approved is not None:
            statement = statement.where(TargetSite.approved_for_automation.is_(approved))
        return self._session.scalars(statement).all()
