import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from sqlalchemy import func, select

from app import main as main_module
from app.cursor import decode_cursor, encode_cursor, filters_fingerprint
from app.database import SessionLocal
from app.dependencies import get_current_user
from app.errors import ApiError, install_error_handlers
from app.main import app
from app.models import ExchangeRate
from app.receipts import MemoryReceiptStorage, detect_image_type, validate_receipt
from app.schemas import ExpensePatch
from app.seed_rates import DEMO_RATES, seed_rates
from tests.helpers import create_account


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "app": app,
        }
    )


def test_signed_cursor_rejects_tampering_and_filter_mismatch():
    fingerprint = filters_fingerprint({"currency": "USD"})
    cursor = encode_cursor(
        date(2026, 10, 1),
        uuid.uuid4(),
        datetime.now(timezone.utc),
        fingerprint,
    )
    with pytest.raises(ApiError, match="cursor is invalid"):
        decode_cursor(cursor[:-2] + "aa", fingerprint)
    with pytest.raises(ApiError, match="cursor is invalid"):
        decode_cursor(cursor, filters_fingerprint({"currency": "EUR"}))


@pytest.mark.asyncio
async def test_authentication_dependency_rejects_missing_and_wrong_token_types(client):
    async with SessionLocal() as session:
        with pytest.raises(ApiError, match="access token is required"):
            await get_current_user(None, session)
    account = await create_account(client)
    async with SessionLocal() as session:
        from fastapi.security import HTTPAuthorizationCredentials

        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=account["tokens"]["refresh_token"]
        )
        with pytest.raises(ApiError, match="token is invalid"):
            await get_current_user(credentials, session)


@pytest.mark.asyncio
async def test_expense_patch_requires_nonempty_and_nonnull_required_fields():
    with pytest.raises(ValueError, match="At least one field"):
        ExpensePatch()
    with pytest.raises(ValueError, match="amount cannot be null"):
        ExpensePatch(amount=None)
    with pytest.raises(ValueError, match="category_id cannot be null"):
        ExpensePatch(category_id=None)
    with pytest.raises(ValueError, match="currency cannot be null"):
        ExpensePatch(currency=None)
    with pytest.raises(ValueError, match="spent_on cannot be null"):
        ExpensePatch(spent_on=None)


def test_receipt_type_detection_and_validation():
    png = b"\x89PNG\r\n\x1a\ncontent"
    jpeg = b"\xff\xd8\xffcontent"
    pdf = b"%PDF-1.7 content"
    assert detect_image_type(png) == ("image/png", ".png")
    assert detect_image_type(jpeg) == ("image/jpeg", ".jpg")
    assert detect_image_type(pdf) == ("application/pdf", ".pdf")
    assert detect_image_type(b"not an image") is None
    assert validate_receipt(png, "file.png") == ("image/png", ".png")
    assert validate_receipt(jpeg, "file.jpeg") == ("image/jpeg", ".jpg")
    assert validate_receipt(pdf, "file.pdf") == ("application/pdf", ".pdf")
    for content, name, message in (
        (b"", "empty.png", "empty"),
        (b"x" * (5 * 1024 * 1024 + 1), "large.pdf", "5 MB"),
        (b"exe", "bad.png", "PNG, JPEG, or PDF"),
        (png, "wrong.jpg", "does not match"),
    ):
        with pytest.raises(ValueError, match=message):
            validate_receipt(content, name)


@pytest.mark.asyncio
async def test_memory_receipt_storage_and_seed_command():
    storage = MemoryReceiptStorage()
    await storage.put("receipt-key", b"receipt", "image/png")
    assert storage.objects["receipt-key"] == (b"receipt", "image/png")
    await seed_rates()
    async with SessionLocal() as session:
        rows = await session.execute(
            select(ExchangeRate.currency, func.count()).group_by(ExchangeRate.currency)
        )
        counts = dict(rows.all())
    assert set(counts) == set(DEMO_RATES)
    assert all(count >= 90 for count in counts.values())
    await seed_rates()
    async with SessionLocal() as session:
        repeated_counts = dict(
            (
                await session.execute(
                    select(ExchangeRate.currency, func.count()).group_by(
                        ExchangeRate.currency
                    )
                )
            ).all()
        )
    assert repeated_counts == counts


@pytest.mark.asyncio
async def test_application_lifespan_sets_and_closes_dependencies(monkeypatch):
    fake_redis = AsyncMock()
    monkeypatch.setattr(main_module.Redis, "from_url", lambda *args, **kwargs: fake_redis)
    async with main_module.lifespan(app):
        assert app.state.redis is fake_redis
        assert isinstance(app.state.receipt_storage, MemoryReceiptStorage)
        fake_redis.ping.assert_awaited_once()
    fake_redis.aclose.assert_awaited_once()
    assert await main_module.health() == {"status": "ok"}


@pytest.mark.asyncio
async def test_expense_patch_validation_error_is_translated(client):
    account = await create_account(client)
    response = await client.patch(
        "/api/v1/expenses/00000000-0000-0000-0000-000000000001",
        json={},
        headers={**account["headers"], "If-Match": '"1"'},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_monthly_report_handles_calendar_year_boundaries_and_empty_categories():
    # Exercise report date-boundary SQL with an authenticated model object.
    async with SessionLocal() as session:
        from app.models import User

        user = await session.scalar(select(User).where(User.email == ""))
        if user is None:
            user = User(
                email=f"calendar-{uuid.uuid4()}@example.com",
                password_hash="unused",
                base_currency="USD",
            )
            session.add(user)
            await session.commit()
        from app.routers.reports import monthly_report

        january = await monthly_report(user, session, 2026, 1)
        december = await monthly_report(user, session, 2026, 12)
        assert january.categories == []
        assert december.categories == []


@pytest.mark.asyncio
async def test_error_handlers_keep_error_envelope_for_api_errors():
    from fastapi import FastAPI

    test_app = FastAPI()
    install_error_handlers(test_app)
    handler = test_app.exception_handlers[ApiError]
    response = await handler(
        _request(),
        ApiError(409, "conflict", "Conflict", [{"field": "name"}]),
    )
    assert response.status_code == 409
    assert response.body == (
        b'{"error":{"code":"conflict","message":"Conflict","details":[{"field":"name"}]}}'
    )


@pytest.mark.asyncio
async def test_login_handler_increments_and_blocks_after_limit(client):
    from app.routers.auth import login
    from app.schemas import LoginRequest

    email = f"missing-{uuid.uuid4()}@example.com"
    for _ in range(5):
        with pytest.raises(ApiError, match="Email or password is incorrect"):
            async with SessionLocal() as session:
                await login(
                    LoginRequest(email=email, password="bad"),
                    _request(),
                    session,
                )
    with pytest.raises(ApiError, match="Too many failed"):
        async with SessionLocal() as session:
            await login(LoginRequest(email=email, password="bad"), _request(), session)
    with pytest.raises(ApiError, match="Too many failed"):
        async with SessionLocal() as session:
            await login(LoginRequest(email=email, password="bad"), _request(), session)


@pytest.mark.asyncio
async def test_bad_refresh_and_logout_tokens_are_unauthorized():
    from app.routers.auth import logout, refresh
    from app.schemas import TokenRequest

    async with SessionLocal() as session:
        for handler in (refresh, logout):
            with pytest.raises(ApiError, match="invalid or expired"):
                await handler(TokenRequest(refresh_token="bad-token"), session)


@pytest.mark.asyncio
async def test_category_without_budget_has_no_percent_value(client):
    account = await create_account(client)
    response = await client.post(
        "/api/v1/categories",
        json={"name": "No budget"},
        headers=account["headers"],
    )
    assert response.status_code == 201
    assert response.json()["budget_percent_used"] is None
