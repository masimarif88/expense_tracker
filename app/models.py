import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_name"),
        CheckConstraint("monthly_budget IS NULL OR monthly_budget >= 0", name="ck_category_budget_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_user_spent_on_id", "user_id", "spent_on", "id"),
        Index("ix_expenses_user_category_spent_on", "user_id", "category_id", "spent_on"),
        Index("ix_expenses_user_currency", "user_id", "currency"),
        Index(
            "ix_expenses_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
        CheckConstraint("amount > 0", name="ck_expense_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    spent_on: Mapped[date] = mapped_column(Date, nullable=False)
    receipt_key: Mapped[str | None] = mapped_column(String(512))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __mapper_args__ = {"version_id_col": version}


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        CheckConstraint("rate_to_usd > 0", name="ck_exchange_rate_positive"),
    )

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    valid_on: Mapped[date] = mapped_column(Date, primary_key=True)
    rate_to_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_active", "user_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("expenses.id", ondelete="CASCADE"), unique=True
    )
    response_body: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_notifications_user_event"),
        Index("ix_notifications_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    month_start: Mapped[date] = mapped_column(Date, nullable=False)
    actual_spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    monthly_budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
