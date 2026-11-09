"""drop the rank-source choice

The Google Custom Search API channel is gone: a journey now searches Google in
the browser, walks the result pages and clicks the target's own result, which is
the only rank source this application has. The per-scenario choice it needed -
``scenarios.search_profile`` - therefore describes nothing, and is dropped with
it. ``search_observations.provider`` keeps saying which channel produced an older
record, so history read back from a pre-0005 database still tells the truth.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The enum this column used, rebuilt for downgrade only: a column can go back to
# existing, but the version it comes back to has nothing to choose between.
_PROFILE = sa.Enum(
    "GOOGLE_BROWSER", "GOOGLE_API", name="searchprofile", native_enum=False, length=32
)


def upgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.drop_column("search_profile")


def downgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.add_column(
            sa.Column("search_profile", _PROFILE, nullable=False, server_default="GOOGLE_BROWSER")
        )
