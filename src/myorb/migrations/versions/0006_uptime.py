"""Add uptime monitors and incidents

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("up", sa.Boolean(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tls_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "monitor_id",
            sa.Integer(),
            sa.ForeignKey("monitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("incidents")
    op.drop_table("monitors")
