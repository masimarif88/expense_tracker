import asyncio
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://expense:expense@localhost:15439/expense_tracker_test",
)
database_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?", 1)[0]
if "test" not in database_name.lower():
    raise RuntimeError("TEST_DATABASE_URL must point to a database with 'test' in its name")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["APP_ENV"] = "test"
os.environ.setdefault("JWT_SECRET", "pytest-only-secret-not-for-production")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ExchangeRate  # noqa: E402
from app.receipts import MemoryReceiptStorage  # noqa: E402


def pytest_sessionstart(session: pytest.Session) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_database_and_redis():
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE notifications, idempotency_records, refresh_tokens, "
                "expenses, exchange_rates, categories, users RESTART IDENTITY CASCADE"
            )
        )

    today = date.today()
    rate_dates = {
        date(2020, 1, 1): {
            "USD": "1.000000",
            "PKR": "280.000000",
            "AED": "3.500000",
            "EUR": "0.800000",
        },
        date(2026, 9, 1): {
            "USD": "1.000000",
            "PKR": "280.000000",
            "AED": "3.500000",
            "EUR": "0.800000",
        },
        date(2026, 10, 1): {
            "USD": "1.000000",
            "PKR": "290.000000",
            "AED": "3.600000",
        },
        today.replace(day=1): {
            "USD": "1.000000",
            "PKR": "278.000000",
            "AED": "3.672500",
            "EUR": "0.920000",
        },
        today: {
            "USD": "1.000000",
            "PKR": "278.000000",
            "AED": "3.672500",
            "EUR": "0.920000",
        },
    }
    rates = [
        ExchangeRate(
            currency=currency,
            valid_on=valid_on,
            rate_to_usd=Decimal(value),
        )
        for valid_on, currencies in rate_dates.items()
        for currency, value in currencies.items()
    ]
    async with SessionLocal() as session:
        session.add_all(rates)
        await session.commit()

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.redis = fake_redis
    app.state.receipt_storage = MemoryReceiptStorage()
    yield
    await fake_redis.aclose()
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
