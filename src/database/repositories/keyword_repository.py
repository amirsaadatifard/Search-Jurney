"""Repository for :class:`Keyword`."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import func, select

from src.database.models.keyword import Keyword
from src.database.repositories.base import Repository, like_pattern


class KeywordRepository(Repository[Keyword]):
    model = Keyword

    def get_by_text(self, text: str) -> Keyword | None:
        """Case-insensitive lookup (keyword uniqueness ignores case)."""
        statement = select(Keyword).where(func.lower(Keyword.text) == text.strip().lower())
        return self._session.scalars(statement).first()

    def existing_texts(self, texts: Iterable[str]) -> set[str]:
        """Return the lower-cased texts that are already stored."""
        wanted = {text.strip().lower() for text in texts}
        if not wanted:
            return set()
        statement = select(func.lower(Keyword.text)).where(func.lower(Keyword.text).in_(wanted))
        return set(self._session.scalars(statement).all())

    def search(
        self, text: str = "", enabled: bool | None = None, tag: str | None = None
    ) -> Sequence[Keyword]:
        """Filter by substring (text or notes), enabled flag and exact tag."""
        statement = select(Keyword).order_by(func.lower(Keyword.text))
        if text.strip():
            pattern = like_pattern(text.strip())
            statement = statement.where(
                Keyword.text.ilike(pattern, escape="\\") | Keyword.notes.ilike(pattern, escape="\\")
            )
        if enabled is not None:
            statement = statement.where(Keyword.enabled.is_(enabled))
        keywords = self._session.scalars(statement).all()
        if tag:
            # Tags live in a JSON array; filtering in Python keeps the query portable.
            wanted = tag.strip().lower()
            keywords = [kw for kw in keywords if wanted in {t.lower() for t in kw.tags}]
        return keywords

    def all_tags(self) -> list[str]:
        """Return every distinct tag, sorted case-insensitively."""
        tags: set[str] = set()
        for tag_list in self._session.scalars(select(Keyword.tags)).all():
            tags.update(tag_list or [])
        return sorted(tags, key=str.lower)
