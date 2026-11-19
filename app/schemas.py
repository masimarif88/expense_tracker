import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$", min_length=3, max_length=3)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=128)]
    base_currency: Currency


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    base_currency: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class CategoryCreate(BaseModel):
    name: Annotated[
        str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)
    ]
    monthly_budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    monthly_budget: Decimal | None
    monthly_spend: Decimal
    budget_percent_used: Decimal | None


class ExpenseCreate(BaseModel):
    category_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Currency
    description: str | None = Field(default=None, max_length=1000)
    spent_on: date
    receipt_key: str | None = Field(default=None, max_length=512)


class ExpensePatch(BaseModel):
    category_id: uuid.UUID | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    currency: Currency | None = None
    description: str | None = Field(default=None, max_length=1000)
    spent_on: date | None = None
    receipt_key: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_field(self) -> "ExpensePatch":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "category_id" in self.model_fields_set and self.category_id is None:
            raise ValueError("category_id cannot be null")
        for field in ("amount", "currency", "spent_on"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ExpenseOut(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    currency: str
    description: str | None
    spent_on: date
    receipt_key: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExpensePage(BaseModel):
    items: list[ExpenseOut]
    next_cursor: str | None


class MonthlyCategoryReport(BaseModel):
    category_id: uuid.UUID
    category_name: str
    total: Decimal
    budget: Decimal | None
    percent_used: Decimal | None
    previous_month_total: Decimal
    change_amount: Decimal
    change_percent: Decimal | None


class MonthlyReport(BaseModel):
    year: int
    month: int
    base_currency: str
    categories: list[MonthlyCategoryReport]


class CsvImportError(BaseModel):
    row: int
    message: str


class CsvImportResult(BaseModel):
    imported_count: int
    rejected_count: int
    errors: list[CsvImportError]


class BudgetAlertEvent(BaseModel):
    event_id: uuid.UUID
    category_id: uuid.UUID
    month_start: date
    actual_spend: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class NotificationOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    category_id: uuid.UUID
    month_start: date
    actual_spend: Decimal
    monthly_budget: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
