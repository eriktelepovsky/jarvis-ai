"""Add password and passkey tables

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("session_key", sa.LargeBinary(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "passkeys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("passkeys")
    op.drop_table("auth_settings")
