"""Repositories for browser and proxy profiles."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from src.database.models.browser_profile import BrowserProfile
from src.database.models.proxy_profile import ProxyProfile
from src.database.repositories.base import Repository


class BrowserProfileRepository(Repository[BrowserProfile]):
    model = BrowserProfile

    def get_by_name(self, name: str) -> BrowserProfile | None:
        statement = select(BrowserProfile).where(
            func.lower(BrowserProfile.name) == name.strip().lower()
        )
        return self._session.scalars(statement).first()

    def list_sorted(self) -> Sequence[BrowserProfile]:
        statement = select(BrowserProfile).order_by(func.lower(BrowserProfile.name))
        return self._session.scalars(statement).all()


class ProxyProfileRepository(Repository[ProxyProfile]):
    model = ProxyProfile

    def get_by_name(self, name: str) -> ProxyProfile | None:
        statement = select(ProxyProfile).where(
            func.lower(ProxyProfile.name) == name.strip().lower()
        )
        return self._session.scalars(statement).first()

    def list_sorted(self, enabled_only: bool = False) -> Sequence[ProxyProfile]:
        statement = select(ProxyProfile).order_by(func.lower(ProxyProfile.name))
        if enabled_only:
            statement = statement.where(ProxyProfile.enabled.is_(True))
        return self._session.scalars(statement).all()
