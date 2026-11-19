import uuid

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.dependencies import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import Category, Notification
from app.schemas import BudgetAlertEvent, NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post(
    "/budget-alerts",
    response_model=NotificationOut,
    status_code=status.HTTP_201_CREATED,
)
async def process_budget_alert(
    payload: BudgetAlertEvent, user: CurrentUser, session: SessionDep
) -> Notification:
    category = await session.scalar(
        select(Category).where(
            Category.id == payload.category_id, Category.user_id == user.id
        )
    )
    if category is None:
        raise ApiError(404, "category_not_found", "Category not found")
    if category.monthly_budget is None or category.monthly_budget <= 0:
        raise ApiError(409, "budget_not_configured", "Category has no positive monthly budget")
    if payload.actual_spend < category.monthly_budget:
        raise ApiError(422, "budget_not_exceeded", "Actual spend has not reached the budget")

    notification_id = uuid.uuid4()
    result = await session.execute(
        pg_insert(Notification)
        .values(
            id=notification_id,
            user_id=user.id,
            event_id=payload.event_id,
            category_id=category.id,
            month_start=payload.month_start.replace(day=1),
            actual_spend=payload.actual_spend,
            monthly_budget=category.monthly_budget,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_user_event")
        .returning(Notification.id)
    )
    inserted_id = result.scalar_one_or_none()
    notification = await session.scalar(
        select(Notification).where(
            Notification.user_id == user.id,
            Notification.event_id == payload.event_id,
        )
    )
    if notification is None:
        raise ApiError(409, "notification_conflict", "Budget alert event could not be resolved")
    if inserted_id is None and (
        notification.category_id != category.id
        or notification.month_start != payload.month_start.replace(day=1)
        or notification.actual_spend != payload.actual_spend
    ):
        raise ApiError(
            409,
            "event_id_reused",
            "The event_id was already used with different alert details",
        )
    await session.commit()
    return notification
