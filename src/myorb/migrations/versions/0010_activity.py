"""Add the activity log

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tone", sa.String(10), nullable=False),
        sa.Column("text", sa.String(300), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
    )
    op.create_index("ix_activity_at", "activity", ["at"])


def downgrade() -> None:
    op.drop_index("ix_activity_at", "activity")
    op.drop_table("activity")
