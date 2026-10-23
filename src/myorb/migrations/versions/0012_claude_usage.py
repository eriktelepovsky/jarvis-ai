"""Keep each Claude response's tokens and price, for what myOrb spends on Claude

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claude_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_5m", sa.Integer(), nullable=False),
        sa.Column("cache_write_1h", sa.Integer(), nullable=False),
        sa.Column("cache_read", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
    )
    op.create_index("ix_claude_usage_at", "claude_usage", ["at"])


def downgrade() -> None:
    op.drop_index("ix_claude_usage_at", "claude_usage")
    op.drop_table("claude_usage")
