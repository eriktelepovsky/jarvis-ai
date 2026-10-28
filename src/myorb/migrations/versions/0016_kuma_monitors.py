"""Add monitors read from Uptime Kuma

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitors") as batch:
        batch.add_column(sa.Column("kuma_connection", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("kuma_key", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("monitors") as batch:
        batch.drop_column("kuma_key")
        batch.drop_column("kuma_connection")
