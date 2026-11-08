"""Proxy profiles for controlled network testing."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, TimestampMixin, enum_type
from src.domain.enums import ProxyProtocol, ProxyStatus


class ProxyProfile(TimestampMixin, Base):
    """Connection details of one proxy.

    The password is **not** stored here. ``has_password`` only records that a
    secret exists in the credential store under this profile's key.
    """

    __tablename__ = "proxy_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    protocol: Mapped[ProxyProtocol] = mapped_column(
        enum_type(ProxyProtocol), default=ProxyProtocol.HTTP, nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    has_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    last_status: Mapped[ProxyStatus] = mapped_column(
        enum_type(ProxyStatus), default=ProxyStatus.UNKNOWN, nullable=False
    )
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"ProxyProfile(id={self.id!r}, name={self.name!r})"
