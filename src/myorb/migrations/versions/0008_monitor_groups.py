"""Add groups to uptime monitors

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitors") as batch:
        batch.add_column(sa.Column("group_name", sa.String(60), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("monitors") as batch:
        batch.drop_column("group_name")
