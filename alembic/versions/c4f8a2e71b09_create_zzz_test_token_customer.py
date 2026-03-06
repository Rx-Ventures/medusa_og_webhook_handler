"""create zzz_test_token_customer

Revision ID: c4f8a2e71b09
Revises: a973ce311ce7
Create Date: 2026-02-26 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4f8a2e71b09"
down_revision: Union[str, None] = "a973ce311ce7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zzz_test_token_customer",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("customer_id", sa.String(length=255), nullable=False),
        sa.Column("order_id", sa.String(length=255), nullable=False),
        sa.Column("payment_token", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_zzz_test_token_customer_customer_id"),
        "zzz_test_token_customer",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_zzz_test_token_customer_order_id"),
        "zzz_test_token_customer",
        ["order_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_zzz_test_token_customer_order_id"),
        table_name="zzz_test_token_customer",
    )
    op.drop_index(
        op.f("ix_zzz_test_token_customer_customer_id"),
        table_name="zzz_test_token_customer",
    )
    op.drop_table("zzz_test_token_customer")
