"""Approved test websites: the automation allowlist."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base, TimestampMixin, enum_type
from src.domain.enums import Environment

if TYPE_CHECKING:
    from src.database.models.scenario import Scenario


class TargetSite(TimestampMixin, Base):
    """A website the user controls and has (or has not yet) approved for automation."""

    __tablename__ = "target_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    environment: Mapped[Environment] = mapped_column(
        enum_type(Environment), default=Environment.LOCAL, nullable=False
    )
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_for_automation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    # passive_deletes="all": leave referencing rows alone so the database's
    # ON DELETE RESTRICT decides, instead of the ORM nulling the foreign key.
    scenarios: Mapped[list[Scenario]] = relationship(
        back_populates="target_site", passive_deletes="all"
    )

    @property
    def is_automation_allowed(self) -> bool:
        """Both switches must be on before any session may touch this site."""
        return self.enabled and self.approved_for_automation

    def __repr__(self) -> str:
        return f"TargetSite(id={self.id!r}, domain={self.domain!r})"
