import uuid
from datetime import date
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from redis.exceptions import RedisError
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.errors import ApiError
from app.main import app
from app.routers import auth, categories, expenses, notifications, reports
from app.schemas import (
    BudgetAlertEvent,
    CategoryCreate,
    ExpenseCreate,
    ExpensePatch,
    LoginRequest,
    RegisterRequest,
    TokenRequest,
)
from app.database import SessionLocal
from tests.helpers import create_account


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "app": app,
        }
    )


@pytest.mark.asyncio
async def test_route_handlers_exercise_postgres_and_storage_boundaries(client):
    async with SessionLocal() as session:
        user = await auth.register(
            RegisterRequest(
                email=f"direct-{uuid.uuid4()}@example.com",
                password="DirectTestPassword123!",
                base_currency="PKR",
            ),
            session,
        )
        request = _request()
        pair = await auth.login(
            LoginRequest(email=user.email, password="DirectTestPassword123!"),
            request,
            session,
        )
        rotated = await auth.refresh(TokenRequest(refresh_token=pair.refresh_token), session)
        logout_response = await auth.logout(
            TokenRequest(refresh_token=rotated.refresh_token), session
        )
        assert logout_response.status_code == 204
        with pytest.raises(ApiError, match="reuse detected"):
            await auth.refresh(TokenRequest(refresh_token=rotated.refresh_token), session)

        category = await categories.create_category(
            CategoryCreate(name="Direct food", monthly_budget="100.00"), user, session
        )
        category_list = await categories.list_categories(user, session)
        assert len(category_list) == 1
        assert category_list[0].budget_percent_used == 0

        expense_payload = ExpenseCreate(
            category_id=category.id,
            amount="15.00",
            currency="USD",
            description="directly inserted",
            spent_on=date.today(),
        )
        first = await expenses.create_expense(
            expense_payload, user, session, "direct-idempotency-key"
        )
        replay = await expenses.create_expense(
            expense_payload, user, session, "direct-idempotency-key"
        )
        assert replay.id == first.id
        second = await expenses.create_expense(
            ExpenseCreate(
                category_id=category.id,
                amount="2.00",
                currency="PKR",
                description="second row",
                spent_on=date.today(),
            ),
            user,
            session,
        )

        filter_options = {
            "category_id": category.id,
            "date_from": date.today(),
            "date_to": date.today(),
            "min_amount": 1,
            "max_amount": 20,
            "currency": None,
            "q": None,
            "cursor": None,
            "limit": 1,
        }
        first_page = await expenses.list_expenses(user, session, **filter_options)
        assert first_page.next_cursor
        second_page = await expenses.list_expenses(
            user,
            session,
            **{**filter_options, "cursor": first_page.next_cursor},
        )
        assert len(second_page.items) == 1

        response = Response()
        await expenses.get_expense(first.id, user, session, response)
        assert response.headers["ETag"] == '"1"'
        updated_response = Response()
        updated = await expenses.update_expense(
            first.id,
            ExpensePatch(description="updated through handler"),
            user,
            session,
            updated_response,
            '"1"',
        )
        assert updated.version == 2
        with pytest.raises(ApiError, match="version_conflict"):
            await expenses.update_expense(
                first.id,
                ExpensePatch(description="stale update"),
                user,
                session,
                Response(),
                '"1"',
            )

        imported = await expenses.import_expenses_csv(
            user,
            session,
            UploadFile(
                filename="handler-import.csv",
                file=BytesIO(
                    (
                        "category_id,amount,currency,spent_on,description\n"
                        f"{category.id},3.50,PKR,{date.today().isoformat()},csv row\n"
                        f"{category.id},0,PKR,{date.today().isoformat()},bad row\n"
                    ).encode()
                ),
            ),
        )
        assert imported.imported_count == 1 and imported.rejected_count == 1

        storage = AsyncMock()
        app.state.receipt_storage = storage
        receipt_request = _request()
        uploaded = await expenses.upload_receipt(
            first.id,
            user,
            session,
            receipt_request,
            UploadFile(filename="real.png", file=BytesIO(b"\x89PNG\r\n\x1a\nvalid")),
        )
        assert uploaded.receipt_key.endswith(".png")
        storage.put.assert_awaited_once()
        with pytest.raises(ApiError, match="filename extension"):
            await expenses.upload_receipt(
                first.id,
                user,
                session,
                receipt_request,
                UploadFile(filename="wrong.jpg", file=BytesIO(b"\x89PNG\r\n\x1a\nvalid")),
            )

        event = BudgetAlertEvent(
            event_id=uuid.uuid4(),
            category_id=category.id,
            month_start=date.today(),
            actual_spend="125.00",
        )
        notification = await notifications.process_budget_alert(event, user, session)
        replayed_notification = await notifications.process_budget_alert(
            event, user, session
        )
        assert notification.id == replayed_notification.id

        report = await reports.monthly_report(
            user, session, date.today().year, date.today().month
        )
        assert report.categories[0].total > 0
        deleted = await expenses.delete_expense(second.id, user, session)
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_login_handler_covers_rate_limit_and_success_reset(client):
    account = await create_account(client)
    request = _request()
    await app.state.redis.set(
        f"auth:login-failures:{account['email']}", "5", ex=900
    )
    with pytest.raises(ApiError, match="Too many failed"):
        async with SessionLocal() as session:
            await auth.login(
                LoginRequest(email=account["email"], password="not-it"),
                request,
                session,
            )
    await app.state.redis.delete(f"auth:login-failures:{account['email']}")
    async with SessionLocal() as session:
        pair = await auth.login(
            LoginRequest(
                email=account["email"], password="CorrectHorseBattery9!"
            ),
            request,
            session,
        )
    assert pair.refresh_token
    assert await app.state.redis.get(
        f"auth:login-failures:{account['email']}"
    ) is None


@pytest.mark.asyncio
async def test_login_handler_returns_service_unavailable_if_redis_fails(client):
    class BrokenRedis:
        async def get(self, key):
            raise RedisError("Redis unavailable")

    account = await create_account(client)
    app.state.redis = BrokenRedis()
    with pytest.raises(ApiError, match="rate limiting is unavailable"):
        async with SessionLocal() as session:
            await auth.login(
                LoginRequest(email=account["email"], password="wrong"),
                _request(),
                session,
            )
