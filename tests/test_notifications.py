import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Notification
from tests.helpers import create_account, create_category


@pytest.mark.asyncio
async def test_reprocessing_budget_alert_event_creates_one_notification(client):
    account = await create_account(client)
    category = await create_category(
        client, account["headers"], budget="100.00"
    )
    payload = {
        "event_id": str(uuid.uuid4()),
        "category_id": category["id"],
        "month_start": "2026-10-15",
        "actual_spend": "125.00",
    }
    results = await asyncio.gather(
        client.post(
            "/api/v1/notifications/budget-alerts",
            json=payload,
            headers=account["headers"],
        ),
        client.post(
            "/api/v1/notifications/budget-alerts",
            json=payload,
            headers=account["headers"],
        ),
    )
    assert [response.status_code for response in results] == [201, 201]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert results[0].json()["month_start"] == "2026-10-01"
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(Notification))
    assert count == 1


@pytest.mark.asyncio
async def test_budget_alert_event_id_cannot_be_reused_for_different_event(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"], budget="100.00")
    event_id = str(uuid.uuid4())
    base = {
        "event_id": event_id,
        "category_id": category["id"],
        "month_start": "2026-10-01",
        "actual_spend": "125.00",
    }
    first = await client.post(
        "/api/v1/notifications/budget-alerts", json=base, headers=account["headers"]
    )
    second = await client.post(
        "/api/v1/notifications/budget-alerts",
        json={**base, "actual_spend": "150.00"},
        headers=account["headers"],
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "event_id_reused"


@pytest.mark.asyncio
async def test_budget_alert_requires_owned_category_and_exceeded_budget(client):
    owner = await create_account(client)
    other = await create_account(client)
    category = await create_category(client, owner["headers"], budget="100.00")
    payload = {
        "event_id": str(uuid.uuid4()),
        "category_id": category["id"],
        "month_start": "2026-10-01",
        "actual_spend": "50.00",
    }
    foreign = await client.post(
        "/api/v1/notifications/budget-alerts",
        json=payload,
        headers=other["headers"],
    )
    under_budget = await client.post(
        "/api/v1/notifications/budget-alerts",
        json=payload,
        headers=owner["headers"],
    )
    assert foreign.status_code == 404
    assert under_budget.status_code == 422
