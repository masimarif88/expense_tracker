import asyncio
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal, engine
from app.models import ExchangeRate

# Local demonstration rates: currency units per one USD, not a market data feed.
DEMO_RATES = {
    "USD": Decimal("1.000000"),
    "PKR": Decimal("278.000000"),
    "AED": Decimal("3.672500"),
    "EUR": Decimal("0.920000"),
}


async def seed_rates() -> None:
    today = date.today()
    rows = [
        {
            "currency": currency,
            "valid_on": today - timedelta(days=offset),
            "rate_to_usd": rate,
        }
        for offset in range(89, -1, -1)
        for currency, rate in DEMO_RATES.items()
    ]
    statement = insert(ExchangeRate).values(rows).on_conflict_do_nothing(
        index_elements=[ExchangeRate.currency, ExchangeRate.valid_on]
    )
    async with SessionLocal() as session:
        await session.execute(statement)
        await session.commit()


async def main() -> None:
    try:
        await seed_rates()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
