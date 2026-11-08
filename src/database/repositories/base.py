"""Generic repository with the operations every aggregate needs."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.base import Base
from src.domain.exceptions import EntityNotFoundError


class Repository[ModelT: Base]:
    """CRUD helper bound to one ORM model and one unit-of-work session.

    Repositories never commit; the surrounding ``Database.session_scope()``
    owns the transaction.
    """

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, entity_id: int) -> ModelT | None:
        return self._session.get(self.model, entity_id)

    def require(self, entity_id: int) -> ModelT:
        """Return the entity or raise :class:`EntityNotFoundError`."""
        entity = self.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(
                f"{self.model.__name__} #{entity_id} does not exist", entity_id=entity_id
            )
        return entity

    def list(self) -> Sequence[ModelT]:
        return self._session.scalars(select(self.model)).unique().all()

    def add(self, entity: ModelT) -> ModelT:
        """Persist ``entity`` and flush so its primary key is available."""
        self._session.add(entity)
        self._session.flush()
        return entity

    def delete(self, entity: ModelT) -> None:
        self._session.delete(entity)
        self._session.flush()

    def count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(self.model)) or 0


def like_pattern(text: str) -> str:
    """Build a ``LIKE`` pattern matching ``text`` anywhere, escaping wildcards."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
