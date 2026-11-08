"""google search channels

A scenario now *chooses* how its ranking is observed: ``GOOGLE_BROWSER`` reads
Google's own results page in the test browser and needs no API key, ``GOOGLE_API``
is the official Custom Search JSON API. Every scenario stored while only one
channel existed is moved to the key-free one, which is what that single value
meant to the user: nothing to configure.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 14:02:55.411902
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_PROFILE = sa.Enum("GOOGLE", name="searchprofile", native_enum=False, length=32)
_NEW_PROFILE = sa.Enum(
    "GOOGLE_BROWSER", "GOOGLE_API", name="searchprofile", native_enum=False, length=32
)
_DEMO_NAME = "Demo - Sample site journey"
# Word-identical to src.services.first_run_service.DEMO_SCENARIO_DESCRIPTION (a
# migration cannot import application code and stay replayable).
_DEMO_DESCRIPTION = (
    "Opens the bundled sample website and scrolls through it. Google cannot list a "
    "loopback address, so this journey claims no ranking; point a scenario at a site "
    "you own to read its real position from Google's results page in the browser."
)


def upgrade() -> None:
    # The update runs before the table is rebuilt, so the new CHECK constraint (if
    # the database has one) only ever sees values it accepts.
    op.execute("UPDATE scenarios SET search_profile = 'GOOGLE_BROWSER'")
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.alter_column(
            "search_profile",
            existing_type=_OLD_PROFILE,
            type_=_NEW_PROFILE,
            existing_nullable=False,
        )
    op.execute(
        "UPDATE scenarios "
        f"SET description = '{_DEMO_DESCRIPTION}' "
        f"WHERE name = '{_DEMO_NAME}' AND description LIKE '%real position first%'"
    )


def downgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.alter_column(
            "search_profile",
            existing_type=_NEW_PROFILE,
            type_=_OLD_PROFILE,
            existing_nullable=False,
        )
    # A scenario that had chosen the API loses that choice: the old schema cannot
    # express which channel was used, only that a real provider was asked.
    op.execute("UPDATE scenarios SET search_profile = 'GOOGLE'")
