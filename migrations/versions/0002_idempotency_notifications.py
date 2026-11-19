"""Add idempotent expense requests and budget notifications.

Revision ID: 0002_idempotency_notifications
Revises: 0001_initial
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_idempotency_notifications"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("expense_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("expense_id"),
        sa.UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("actual_spend", sa.Numeric(12, 2), nullable=False),
        sa.Column("monthly_budget", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "event_id", name="uq_notifications_user_event"),
    )
    op.create_index(
        "ix_notifications_user_created_at",
        "notifications",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_created_at", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("idempotency_records")
