"""Add repeating tasks

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("repeat", sa.String(40), nullable=True))
        batch.add_column(sa.Column("last_due", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("last_due")
        batch.drop_column("repeat")
