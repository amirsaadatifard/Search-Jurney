"""Real search observations (Google Research mode) and their individual results."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base, utcnow


class SearchObservation(Base):
    """One search that was actually performed through a search provider.

    Observations are never invented: a row exists only if the provider returned
    an answer. ``target_position`` is the rank within the *returned* results.
    """

    __tablename__ = "search_observations"
    __table_args__ = (Index("ix_search_observations_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    target_domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    target_position: Mapped[int | None] = mapped_column(Integer)
    target_url: Mapped[str | None] = mapped_column(String(2000))
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    language: Mapped[str] = mapped_column(String(12), default="", nullable=False)
    country: Mapped[str] = mapped_column(String(12), default="", nullable=False)
    requested_results: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    results: Mapped[list[ObservedResult]] = relationship(
        back_populates="observation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ObservedResult.position",
    )

    def __repr__(self) -> str:
        return (
            f"SearchObservation(id={self.id!r}, query={self.query!r}, found={self.target_found!r})"
        )


class ObservedResult(Base):
    """A single result of an observation."""

    __tablename__ = "observed_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("search_observations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    domain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_target: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    observation: Mapped[SearchObservation] = relationship(back_populates="results")
