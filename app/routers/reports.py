from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.dependencies import CurrentUser, SessionDep
from app.errors import ApiError
from app.schemas import MonthlyCategoryReport, MonthlyReport

router = APIRouter(prefix="/reports", tags=["reports"])

MONTHLY_REPORT_SQL = text(
    """
    WITH expense_conversions AS (
        SELECT
            e.category_id,
            date_trunc('month', e.spent_on)::date AS month_start,
            e.id AS expense_id,
            CASE
                WHEN source_rate.rate_to_usd IS NOT NULL
                 AND base_rate.rate_to_usd IS NOT NULL
                THEN e.amount / source_rate.rate_to_usd * base_rate.rate_to_usd
                ELSE NULL
            END AS converted_amount,
            (source_rate.rate_to_usd IS NULL OR base_rate.rate_to_usd IS NULL)
                AS missing_rate
        FROM expenses AS e
        CROSS JOIN users AS u
        LEFT JOIN LATERAL (
            SELECT er.rate_to_usd
            FROM exchange_rates AS er
            WHERE er.currency = e.currency AND er.valid_on <= e.spent_on
            ORDER BY er.valid_on DESC
            LIMIT 1
        ) AS source_rate ON TRUE
        LEFT JOIN LATERAL (
            SELECT er.rate_to_usd
            FROM exchange_rates AS er
            WHERE er.currency = u.base_currency AND er.valid_on <= e.spent_on
            ORDER BY er.valid_on DESC
            LIMIT 1
        ) AS base_rate ON TRUE
        WHERE e.user_id = :user_id
          AND u.id = e.user_id
          AND e.spent_on >= :previous_start
          AND e.spent_on < :following_start
    ),
    months AS (
        SELECT CAST(:previous_start AS date) AS month_start
        UNION ALL
        SELECT CAST(:month_start AS date) AS month_start
    ),
    category_months AS (
        SELECT
            c.id AS category_id,
            c.name AS category_name,
            c.monthly_budget AS budget,
            m.month_start,
            COALESCE(SUM(ec.converted_amount), 0)::numeric(18, 2) AS total,
            COUNT(ec.expense_id) FILTER (WHERE ec.missing_rate) AS missing_rate_count
        FROM categories AS c
        CROSS JOIN months AS m
        LEFT JOIN expense_conversions AS ec
            ON ec.category_id = c.id AND ec.month_start = m.month_start
        WHERE c.user_id = :user_id
        GROUP BY c.id, m.month_start
    ),
    with_previous_month AS (
        SELECT
            category_id,
            category_name,
            budget,
            month_start,
            total,
            missing_rate_count,
            LAG(total) OVER (
                PARTITION BY category_id ORDER BY month_start
            ) AS previous_total,
            LAG(missing_rate_count) OVER (
                PARTITION BY category_id ORDER BY month_start
            ) AS previous_missing_rate_count
        FROM category_months
    )
    SELECT
        category_id,
        category_name,
        total,
        budget,
        CASE
            WHEN budget IS NULL OR budget = 0 THEN NULL
            ELSE ROUND(total / budget * 100, 2)
        END AS percent_used,
        COALESCE(previous_total, 0)::numeric(18, 2) AS previous_month_total,
        (total - COALESCE(previous_total, 0))::numeric(18, 2) AS change_amount,
        CASE
            WHEN previous_total IS NULL OR previous_total = 0 THEN NULL
            ELSE ROUND((total - previous_total) / previous_total * 100, 2)
        END AS change_percent,
        missing_rate_count,
        COALESCE(previous_missing_rate_count, 0) AS previous_missing_rate_count
    FROM with_previous_month
    WHERE month_start = CAST(:month_start AS date)
    ORDER BY category_name
    """
)


@router.get("/monthly", response_model=MonthlyReport)
async def monthly_report(
    user: CurrentUser,
    session: SessionDep,
    year: int = Query(ge=2000, le=2200),
    month: int = Query(ge=1, le=12),
) -> MonthlyReport:
    month_start = date(year, month, 1)
    if month == 1:
        previous_start = date(year - 1, 12, 1)
    else:
        previous_start = date(year, month - 1, 1)
    if month == 12:
        following_start = date(year + 1, 1, 1)
    else:
        following_start = date(year, month + 1, 1)

    rows = (
        await session.execute(
            MONTHLY_REPORT_SQL,
            {
                "user_id": user.id,
                "previous_start": previous_start,
                "month_start": month_start,
                "following_start": following_start,
            },
        )
    ).mappings().all()
    incomplete = [
        {
            "category_id": str(row["category_id"]),
            "missing_expense_count": int(row["missing_rate_count"])
            + int(row["previous_missing_rate_count"]),
        }
        for row in rows
        if row["missing_rate_count"] or row["previous_missing_rate_count"]
    ]
    if incomplete:
        raise ApiError(
            422,
            "exchange_rate_missing",
            "An exchange rate is missing for one or more expenses in the report period",
            incomplete,
        )

    categories = [
        MonthlyCategoryReport(
            category_id=row["category_id"],
            category_name=row["category_name"],
            total=row["total"],
            budget=row["budget"],
            percent_used=row["percent_used"],
            previous_month_total=row["previous_month_total"],
            change_amount=row["change_amount"],
            change_percent=row["change_percent"],
        )
        for row in rows
    ]
    return MonthlyReport(
        year=year,
        month=month,
        base_currency=user.base_currency,
        categories=categories,
    )
