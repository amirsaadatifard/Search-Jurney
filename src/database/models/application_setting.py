"""Key/value store for user-editable application settings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, utcnow


class ApplicationSetting(Base):
    """One persisted setting. ``value`` holds a JSON-encoded scalar or object."""

    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"ApplicationSetting(key={self.key!r})"
