"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker


class Database:
    """Owns the SQLAlchemy engine and hands out short-lived sessions.

    One instance is created at bootstrap and injected wherever persistence is
    needed. Sessions are cheap; every unit of work opens its own through
    :meth:`session_scope`, so worker threads never share a session with the GUI.
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._url = url
        self._engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False})
        event.listen(self._engine, "connect", _configure_sqlite_connection)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    @classmethod
    def for_file(cls, path: Path) -> Database:
        """Create a database bound to a SQLite file."""
        return cls(sqlite_url(path))

    @classmethod
    def in_memory(cls) -> Database:
        """Create an isolated in-memory database (used by tests)."""
        from sqlalchemy.pool import StaticPool

        database = cls.__new__(cls)
        database._url = "sqlite://"
        database._engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(database._engine, "connect", _configure_sqlite_connection)
        database._session_factory = sessionmaker(bind=database._engine, expire_on_commit=False)
        return database

    @property
    def url(self) -> str:
        return self._url

    @property
    def engine(self) -> Engine:
        return self._engine

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Provide a transactional scope: commit on success, roll back on error."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        """Return ``True`` if a trivial query succeeds."""
        with self._engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1

    def dispose(self) -> None:
        """Close all pooled connections."""
        self._engine.dispose()


def sqlite_url(path: Path) -> str:
    """Build a SQLAlchemy URL for a SQLite file."""
    return f"sqlite:///{path.as_posix()}"


def _configure_sqlite_connection(dbapi_connection: Any, _record: Any) -> None:
    """Enable foreign keys (off by default in SQLite) and WAL journaling."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
