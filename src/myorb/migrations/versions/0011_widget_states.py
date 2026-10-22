"""Keep each widget's last number and state, for the activity list

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_states",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("failing", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("widget_states")
