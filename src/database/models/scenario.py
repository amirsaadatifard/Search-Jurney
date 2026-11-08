"""Test scenarios."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base, TimestampMixin, enum_type
from src.database.models.keyword import KEYWORD_MAX_LENGTH
from src.domain.enums import SearchProfile

if TYPE_CHECKING:
    from src.database.models.automation_session import AutomationSession
    from src.database.models.browser_profile import BrowserProfile
    from src.database.models.proxy_profile import ProxyProfile
    from src.database.models.target_site import TargetSite


class Scenario(TimestampMixin, Base):
    """What to search for, which approved site to open and how to behave there.

    The ranking a journey starts from is *observed*, never configured:
    :attr:`search_profile` chooses the channel that observes it - Google's own
    results page in the browser, or the official search API - and the position
    found there is stored on the session.
    """

    __tablename__ = "scenarios"
    __table_args__ = (
        CheckConstraint("min_dwell_seconds >= 0", name="min_dwell_non_negative"),
        CheckConstraint("max_dwell_seconds >= min_dwell_seconds", name="dwell_range_ordered"),
        CheckConstraint("iterations > 0", name="iterations_positive"),
        CheckConstraint("delay_between_iterations >= 0", name="delay_non_negative"),
        CheckConstraint("max_scroll_actions >= 0", name="scroll_actions_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    keyword: Mapped[str] = mapped_column(String(KEYWORD_MAX_LENGTH), nullable=False, index=True)

    target_site_id: Mapped[int] = mapped_column(
        ForeignKey("target_sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    browser_profile_id: Mapped[int] = mapped_column(
        ForeignKey("browser_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    proxy_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("proxy_profiles.id", ondelete="SET NULL"), index=True
    )
    search_profile: Mapped[SearchProfile] = mapped_column(
        enum_type(SearchProfile), default=SearchProfile.GOOGLE_BROWSER, nullable=False
    )

    min_dwell_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_dwell_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    iterations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    delay_between_iterations: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    scroll_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_scroll_actions: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    clear_cookies_between_sessions: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    clear_storage_between_sessions: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    incognito: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    # Eager-loaded: scenarios are always displayed together with these names,
    # and instances are used after their session has closed.
    target_site: Mapped[TargetSite] = relationship(back_populates="scenarios", lazy="joined")
    browser_profile: Mapped[BrowserProfile] = relationship(lazy="joined")
    proxy_profile: Mapped[ProxyProfile | None] = relationship(lazy="joined")
    sessions: Mapped[list[AutomationSession]] = relationship(
        back_populates="scenario", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"Scenario(id={self.id!r}, name={self.name!r})"
