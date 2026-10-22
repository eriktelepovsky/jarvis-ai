"""Add daily widget snapshots

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_snapshots",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("widget_snapshots")
