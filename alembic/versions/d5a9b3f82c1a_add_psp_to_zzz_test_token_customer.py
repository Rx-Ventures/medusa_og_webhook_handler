"""add psp to zzz_test_token_customer

Revision ID: d5a9b3f82c1a
Revises: c4f8a2e71b09
Create Date: 2026-02-26 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5a9b3f82c1a"
down_revision: Union[str, None] = "c4f8a2e71b09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "zzz_test_token_customer",
        sa.Column("psp", sa.String(length=64), nullable=False, server_default="netvalve"),
    )
    # Remove server default so new rows must provide psp (optional; keeps DB consistent)
    op.alter_column(
        "zzz_test_token_customer",
        "psp",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("zzz_test_token_customer", "psp")
