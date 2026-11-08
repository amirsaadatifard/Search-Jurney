"""google search source

Scenarios now read the ranking of their target site from a real search provider
(the official Google Custom Search JSON API) instead of from a position
configured in the bundled local simulator, and that simulator is gone. So the
simulator's knob is dropped and the only search profile left is ``GOOGLE``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08 09:40:11.204857
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_PROFILE = sa.Enum("LOCAL_SIMULATOR", name="searchprofile", native_enum=False, length=32)
_NEW_PROFILE = sa.Enum("GOOGLE", name="searchprofile", native_enum=False, length=32)
_POSITION_CHECK = "ck_scenarios_simulated_position_range"
_DEMO_OLD_NAME = "Demo - Local search journey"
_DEMO_NEW_NAME = "Demo - Sample site journey"
_DEMO_DESCRIPTION = (
    "Opens the bundled sample website and scrolls through it. Google cannot list a "
    "loopback address, so this journey claims no ranking; point a scenario at a site "
    "you own to see its real position first."
)


def upgrade() -> None:
    # Every stored scenario asks the real provider now. The update runs before the
    # table is rebuilt so the new enum values are already in place for the copy.
    op.execute("UPDATE scenarios SET search_profile = 'GOOGLE'")
    with op.batch_alter_table("scenarios") as batch_op:
        # The check constraint refers to the column that goes next, and SQLite
        # batch mode copies it into the rebuilt table unless it is dropped first.
        batch_op.drop_constraint(_POSITION_CHECK, type_="check")
        batch_op.alter_column(
            "search_profile",
            existing_type=_OLD_PROFILE,
            type_=_NEW_PROFILE,
            existing_nullable=False,
        )
        batch_op.drop_column("simulated_position")
    # The seeded demo scenario is renamed with its new meaning; without this an
    # existing database would gain a second demo row on the next start.
    op.execute(
        "UPDATE scenarios "
        f"SET name = '{_DEMO_NEW_NAME}', description = '{_DEMO_DESCRIPTION}' "
        f"WHERE name = '{_DEMO_OLD_NAME}'"
    )


def downgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.add_column(sa.Column("simulated_position", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "simulated_position_range",
            "simulated_position IS NULL OR simulated_position BETWEEN 1 AND 10",
        )
        batch_op.alter_column(
            "search_profile",
            existing_type=_NEW_PROFILE,
            type_=_OLD_PROFILE,
            existing_nullable=False,
        )
    op.execute("UPDATE scenarios SET search_profile = 'LOCAL_SIMULATOR'")
    op.execute(
        "UPDATE scenarios SET simulated_position = 4 WHERE name = 'Demo - Sample site journey'"
    )
    op.execute(f"UPDATE scenarios SET name = '{_DEMO_OLD_NAME}' WHERE name = '{_DEMO_NEW_NAME}'")
