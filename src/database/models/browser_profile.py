"""Browser configuration profiles."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, TimestampMixin, enum_type
from src.domain.enums import UserAgentMode


class BrowserProfile(TimestampMixin, Base):
    """Viewport, locale and session options for the test browser.

    Empty ``locale`` / ``timezone`` mean "use the browser's own default".
    """

    __tablename__ = "browser_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    viewport_width: Mapped[int] = mapped_column(Integer, default=1280, nullable=False)
    viewport_height: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    headless: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locale: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    downloads_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    javascript_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clear_storage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    user_agent_mode: Mapped[UserAgentMode] = mapped_column(
        enum_type(UserAgentMode), default=UserAgentMode.BROWSER_DEFAULT, nullable=False
    )

    def __repr__(self) -> str:
        return f"BrowserProfile(id={self.id!r}, name={self.name!r})"
