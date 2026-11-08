"""Browser profile management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.database.models import BrowserProfile
from src.database.repositories import BrowserProfileRepository, ScenarioRepository
from src.database.session import Database
from src.domain.exceptions import EntityInUseError, InvalidConfigurationError
from src.domain.schemas import BrowserProfileInput, validate_input


class BrowserProfileService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list(self, text: str = "") -> list[BrowserProfile]:
        needle = text.strip().lower()
        with self._database.session_scope() as session:
            profiles = list(BrowserProfileRepository(session).list_sorted())
        return [profile for profile in profiles if needle in profile.name.lower()]

    def create(self, data: Mapping[str, Any]) -> BrowserProfile:
        values = validate_input(BrowserProfileInput, data)
        with self._database.session_scope() as session:
            repo = BrowserProfileRepository(session)
            if repo.get_by_name(values.name) is not None:
                raise _duplicate(values.name)
            return repo.add(BrowserProfile(**values.model_dump()))

    def update(self, profile_id: int, data: Mapping[str, Any]) -> BrowserProfile:
        values = validate_input(BrowserProfileInput, data)
        with self._database.session_scope() as session:
            repo = BrowserProfileRepository(session)
            profile = repo.require(profile_id)
            existing = repo.get_by_name(values.name)
            if existing is not None and existing.id != profile_id:
                raise _duplicate(values.name)
            for name, value in values.model_dump().items():
                setattr(profile, name, value)
            return profile

    def delete(self, profile_id: int) -> None:
        with self._database.session_scope() as session:
            repo = BrowserProfileRepository(session)
            profile = repo.require(profile_id)
            in_use = ScenarioRepository(session).count_using_browser_profile(profile_id)
            if in_use:
                raise EntityInUseError(
                    f"'{profile.name}' is used by {in_use} scenario(s). "
                    "Change or delete those scenarios first."
                )
            repo.delete(profile)


def _duplicate(name: str) -> InvalidConfigurationError:
    return InvalidConfigurationError(
        "Duplicate name.", field_errors={"name": f"A profile named '{name}' already exists."}
    )
