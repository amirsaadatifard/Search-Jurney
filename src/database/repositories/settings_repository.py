"""Repository for :class:`ApplicationSetting`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models.application_setting import ApplicationSetting


class SettingsRepository:
    """Raw string access to the settings table (JSON handling is the service's job)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> str | None:
        setting = self._session.get(ApplicationSetting, key)
        return None if setting is None else setting.value

    def set(self, key: str, value: str) -> None:
        setting = self._session.get(ApplicationSetting, key)
        if setting is None:
            self._session.add(ApplicationSetting(key=key, value=value))
        else:
            setting.value = value

    def delete(self, key: str) -> None:
        setting = self._session.get(ApplicationSetting, key)
        if setting is not None:
            self._session.delete(setting)

    def all(self) -> dict[str, str]:
        rows = self._session.scalars(select(ApplicationSetting)).all()
        return {row.key: row.value for row in rows}
