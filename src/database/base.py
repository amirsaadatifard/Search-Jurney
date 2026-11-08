"""Declarative base and shared column mixins."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint names make Alembic migrations deterministic, which is
# required for SQLite "batch" migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Return the current time as a naive UTC datetime.

    SQLite has no timezone-aware type, so timestamps are stored as naive UTC
    and converted to local time only when displayed.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def enum_type(enum_class: type[StrEnum], length: int = 32) -> Enum:
    """Column type storing a ``StrEnum`` by *value* in a portable VARCHAR."""
    return Enum(
        enum_class,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` columns maintained by the ORM."""

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
