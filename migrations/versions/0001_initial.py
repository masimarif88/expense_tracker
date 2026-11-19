"""Create expense tracking tables and query indexes.

Revision ID: 0001_initial
Revises:
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("monthly_budget", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "monthly_budget IS NULL OR monthly_budget >= 0",
            name="ck_category_budget_nonnegative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
    )
    op.create_table(
        "exchange_rates",
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("valid_on", sa.Date(), nullable=False),
        sa.Column("rate_to_usd", sa.Numeric(14, 6), nullable=False),
        sa.CheckConstraint("rate_to_usd > 0", name="ck_exchange_rate_positive"),
        sa.PrimaryKeyConstraint("currency", "valid_on"),
    )
    op.create_table(
        "expenses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("receipt_key", sa.String(length=512), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_expense_amount_positive"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_expenses_user_spent_on_id",
        "expenses",
        ["user_id", "spent_on", "id"],
    )
    op.create_index(
        "ix_expenses_user_category_spent_on",
        "expenses",
        ["user_id", "category_id", "spent_on"],
    )
    op.create_index(
        "ix_expenses_user_currency", "expenses", ["user_id", "currency"]
    )
    op.create_index(
        "ix_expenses_description_trgm",
        "expenses",
        ["description"],
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_refresh_tokens_user_active",
        "refresh_tokens",
        ["user_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_user_active", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_expenses_description_trgm", table_name="expenses")
    op.drop_index("ix_expenses_user_currency", table_name="expenses")
    op.drop_index("ix_expenses_user_category_spent_on", table_name="expenses")
    op.drop_index("ix_expenses_user_spent_on_id", table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("exchange_rates")
    op.drop_table("categories")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
