"""Expire idempotency keys and retain their original responses.

Revision ID: 0003_idempotency_expiry_response
Revises: 0002_idempotency_notifications
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_idempotency_expiry_response"
down_revision: Union[str, None] = "0002_idempotency_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column("response_body", sa.JSON(), nullable=True),
    )
    op.add_column(
        "idempotency_records",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE idempotency_records "
        "SET expires_at = created_at + INTERVAL '24 hours'"
    )
    op.alter_column("idempotency_records", "expires_at", nullable=False)
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_idempotency_records_expires_at", table_name="idempotency_records"
    )
    op.drop_column("idempotency_records", "expires_at")
    op.drop_column("idempotency_records", "response_body")
