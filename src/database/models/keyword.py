"""Reusable search keywords."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, TimestampMixin

KEYWORD_MAX_LENGTH = 200


class Keyword(TimestampMixin, Base):
    """A keyword from the reusable library, optionally tagged."""

    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(KEYWORD_MAX_LENGTH), unique=True, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"Keyword(id={self.id!r}, text={self.text!r})"
