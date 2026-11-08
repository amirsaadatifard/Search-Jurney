"""Alembic environment.

Used both by the ``alembic`` command line (development) and by
``src.database.migrator.DatabaseMigrator`` (application startup). In the second
case an open connection is passed through ``config.attributes``.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine

from src.database import models  # noqa: F401  (registers tables on Base.metadata)
from src.database.base import Base

config = context.config
target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite cannot ALTER most things in place
        compare_type=True,
    )


def run_migrations_offline() -> None:
    """Emit SQL without connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations on a live connection."""
    supplied: Connection | None = config.attributes.get("connection")
    if supplied is not None:
        _configure(supplied)
        with context.begin_transaction():
            context.run_migrations()
        return

    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not configured")
    engine = create_engine(url)
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
