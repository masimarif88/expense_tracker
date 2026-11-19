from datetime import date
from decimal import Decimal

from fastapi import APIRouter, status
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import Category, ExchangeRate, Expense
from app.schemas import CategoryCreate, CategoryOut

router = APIRouter(prefix="/categories", tags=["categories"])


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate, user: CurrentUser, session: SessionDep
) -> CategoryOut:
    name = payload.name.strip()
    existing = await session.scalar(
        select(Category.id).where(Category.user_id == user.id, Category.name == name)
    )
    if existing is not None:
        raise ApiError(409, "duplicate_category", "A category with this name already exists")
    category = Category(
        user_id=user.id,
        name=name,
        monthly_budget=payload.monthly_budget,
    )
    session.add(category)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_categories_user_name":
            raise ApiError(
                409, "duplicate_category", "A category with this name already exists"
            ) from exc
        raise
    await session.refresh(category)
    return CategoryOut(
        id=category.id,
        name=category.name,
        monthly_budget=category.monthly_budget,
        monthly_spend=Decimal("0.00"),
        budget_percent_used=(
            Decimal("0.00")
            if category.monthly_budget is not None and category.monthly_budget > 0
            else None
        ),
    )


@router.get("", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, session: SessionDep) -> list[CategoryOut]:
    today = date.today()
    month_start = today.replace(day=1)
    next_month = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    source_rate = (
        select(ExchangeRate.rate_to_usd.label("rate_to_usd"))
        .where(
            ExchangeRate.currency == Expense.currency,
            ExchangeRate.valid_on <= Expense.spent_on,
        )
        .order_by(ExchangeRate.valid_on.desc())
        .limit(1)
        .lateral("source_rate")
    )
    base_rate = (
        select(ExchangeRate.rate_to_usd.label("rate_to_usd"))
        .where(
            ExchangeRate.currency == user.base_currency,
            ExchangeRate.valid_on <= Expense.spent_on,
        )
        .order_by(ExchangeRate.valid_on.desc())
        .limit(1)
        .lateral("base_rate")
    )
    converted = Expense.amount / source_rate.c.rate_to_usd * base_rate.c.rate_to_usd
    missing_rate_count = func.count(Expense.id).filter(
        or_(source_rate.c.rate_to_usd.is_(None), base_rate.c.rate_to_usd.is_(None))
    )
    statement = (
        select(
            Category,
            func.coalesce(func.sum(converted), 0).label("monthly_spend"),
            missing_rate_count.label("missing_rate_count"),
        )
        .outerjoin(
            Expense,
            and_(
                Expense.category_id == Category.id,
                Expense.user_id == user.id,
                Expense.spent_on >= month_start,
                Expense.spent_on < next_month,
            ),
        )
        .outerjoin(source_rate, true())
        .outerjoin(base_rate, true())
        .where(Category.user_id == user.id)
        .group_by(Category.id)
        .order_by(Category.name)
    )
    rows = (await session.execute(statement)).all()

    result: list[CategoryOut] = []
    for category, spend, missing_count in rows:
        if missing_count:
            raise ApiError(
                422,
                "exchange_rate_missing",
                "An exchange rate is missing for one or more expenses in this month",
                [{"category_id": str(category.id), "missing_expense_count": missing_count}],
            )
        monthly_spend = Decimal(spend).quantize(Decimal("0.01"))
        percent = (
            (monthly_spend / category.monthly_budget * 100).quantize(Decimal("0.01"))
            if category.monthly_budget is not None and category.monthly_budget > 0
            else None
        )
        result.append(
            CategoryOut(
                id=category.id,
                name=category.name,
                monthly_budget=category.monthly_budget,
                monthly_spend=monthly_spend,
                budget_percent_used=percent,
            )
        )
    return result
