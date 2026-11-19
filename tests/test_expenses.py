import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from app.database import SessionLocal
from app.models import IdempotencyRecord
from app.main import app
from tests.helpers import create_account, create_category, create_expense


@pytest.mark.asyncio
async def test_create_and_get_expense(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    created = await create_expense(client, account["headers"], category["id"])
    assert created.status_code == 201
    response = await client.get(
        f"/api/v1/expenses/{created.json()['id']}", headers=account["headers"]
    )
    assert response.status_code == 200
    assert response.json()["amount"] == "12.34"
    assert response.headers["ETag"] == '"1"'


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0", "-1.00", "1.001"])
async def test_expense_rejects_zero_negative_and_excess_precision(client, amount):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    response = await create_expense(
        client, account["headers"], category["id"], amount=amount
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_expense_category_must_belong_to_current_user(client):
    owner = await create_account(client)
    other = await create_account(client)
    category = await create_category(client, owner["headers"])
    response = await create_expense(client, other["headers"], category["id"])
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "category_not_found"


@pytest.mark.asyncio
async def test_another_users_expense_is_not_visible(client):
    owner = await create_account(client)
    other = await create_account(client)
    category = await create_category(client, owner["headers"])
    expense = await create_expense(client, owner["headers"], category["id"])
    response = await client.get(
        f"/api/v1/expenses/{expense.json()['id']}", headers=other["headers"]
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "expense_not_found"


@pytest.mark.asyncio
async def test_another_users_expense_cannot_be_patched_or_deleted(client):
    owner = await create_account(client)
    other = await create_account(client)
    category = await create_category(client, owner["headers"])
    expense = await create_expense(client, owner["headers"], category["id"])
    expense_id = expense.json()["id"]
    patched = await client.patch(
        f"/api/v1/expenses/{expense_id}",
        json={"description": "changed"},
        headers={**other["headers"], "If-Match": '"1"'},
    )
    deleted = await client.delete(
        f"/api/v1/expenses/{expense_id}", headers=other["headers"]
    )
    assert patched.status_code == 404
    assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_expense_list_filters(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="12.34",
        currency="PKR",
        spent_on="2026-10-10",
        description="lunch at cafe",
    )
    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="40.00",
        currency="USD",
        spent_on="2026-10-15",
        description="monthly pass",
    )
    response = await client.get(
        "/api/v1/expenses",
        params={
            "category_id": category["id"],
            "date_from": "2026-10-01",
            "date_to": "2026-10-12",
            "min_amount": "10",
            "max_amount": "20",
            "currency": "PKR",
            "q": "cafe",
        },
        headers=account["headers"],
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["description"] == "lunch at cafe"
    assert response.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_expense_rejects_invalid_date_and_amount_ranges(client):
    account = await create_account(client)
    date_response = await client.get(
        "/api/v1/expenses?date_from=2026-10-20&date_to=2026-10-01",
        headers=account["headers"],
    )
    amount_response = await client.get(
        "/api/v1/expenses?min_amount=9&max_amount=1",
        headers=account["headers"],
    )
    assert date_response.status_code == 400
    assert amount_response.status_code == 400
    assert date_response.json()["error"]["code"] == "invalid_date_range"
    assert amount_response.json()["error"]["code"] == "invalid_amount_range"


@pytest.mark.asyncio
async def test_cursor_pagination_returns_each_existing_row_once_after_insert(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expected_ids = set()
    for index in range(5):
        response = await create_expense(
            client,
            account["headers"],
            category["id"],
            spent_on="2026-10-10",
            description=f"expense {index}",
        )
        expected_ids.add(response.json()["id"])

    first = await client.get(
        "/api/v1/expenses?limit=2", headers=account["headers"]
    )
    assert first.status_code == 200
    found = {item["id"] for item in first.json()["items"]}
    cursor = first.json()["next_cursor"]
    inserted = await create_expense(
        client,
        account["headers"],
        category["id"],
        spent_on="2026-10-10",
        description="inserted during paging",
    )
    assert inserted.status_code == 201

    while cursor:
        page = await client.get(
            "/api/v1/expenses",
            params={"limit": 2, "cursor": cursor},
            headers=account["headers"],
        )
        assert page.status_code == 200
        ids = [item["id"] for item in page.json()["items"]]
        assert not found.intersection(ids)
        found.update(ids)
        cursor = page.json()["next_cursor"]
    assert found == expected_ids
    assert inserted.json()["id"] not in found


@pytest.mark.asyncio
async def test_cursor_cannot_be_reused_with_different_filters(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    for _ in range(2):
        await create_expense(client, account["headers"], category["id"])
    first = await client.get("/api/v1/expenses?limit=1", headers=account["headers"])
    response = await client.get(
        "/api/v1/expenses",
        params={"limit": 1, "currency": "USD", "cursor": first.json()["next_cursor"]},
        headers=account["headers"],
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.asyncio
async def test_patch_requires_if_match_and_advances_etag(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    expense_id = expense.json()["id"]
    missing = await client.patch(
        f"/api/v1/expenses/{expense_id}",
        json={"description": "updated"},
        headers=account["headers"],
    )
    assert missing.status_code == 428
    current = await client.get(
        f"/api/v1/expenses/{expense_id}", headers=account["headers"]
    )
    updated = await client.patch(
        f"/api/v1/expenses/{expense_id}",
        json={"description": "updated"},
        headers={**account["headers"], "If-Match": current.headers["ETag"]},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.headers["ETag"] == '"2"'


@pytest.mark.asyncio
async def test_two_parallel_patches_with_same_etag_have_one_winner(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    endpoint = f"/api/v1/expenses/{expense.json()['id']}"
    headers = {**account["headers"], "If-Match": '"1"'}
    responses = await asyncio.gather(
        client.patch(endpoint, json={"description": "parallel one"}, headers=headers),
        client.patch(endpoint, json={"description": "parallel two"}, headers=headers),
    )
    assert sorted(response.status_code for response in responses) == [200, 412]


@pytest.mark.asyncio
async def test_stale_if_match_returns_precondition_failed(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    endpoint = f"/api/v1/expenses/{expense.json()['id']}"
    headers = {**account["headers"], "If-Match": '"1"'}
    assert (await client.patch(endpoint, json={"description": "first"}, headers=headers)).status_code == 200
    stale = await client.patch(endpoint, json={"description": "stale"}, headers=headers)
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "version_conflict"


@pytest.mark.asyncio
async def test_delete_expense_returns_no_content(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    deleted = await client.delete(
        f"/api/v1/expenses/{expense.json()['id']}", headers=account["headers"]
    )
    missing = await client.get(
        f"/api/v1/expenses/{expense.json()['id']}", headers=account["headers"]
    )
    assert deleted.status_code == 204
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_parallel_idempotency_key_creates_one_expense(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    key = str(uuid.uuid4())
    headers = {**account["headers"], "Idempotency-Key": key}
    payload = {
        "category_id": category["id"],
        "amount": "24.99",
        "currency": "PKR",
        "description": "single logical expense",
        "spent_on": "2026-10-10",
    }
    responses = await asyncio.gather(
        client.post("/api/v1/expenses", json=payload, headers=headers),
        client.post("/api/v1/expenses", json=payload, headers=headers),
    )
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert responses[0].json() == responses[1].json()
    page = await client.get("/api/v1/expenses", headers=account["headers"])
    assert len(page.json()["items"]) == 1


@pytest.mark.asyncio
async def test_idempotency_key_with_different_payload_is_unprocessable(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    first = await create_expense(
        client,
        account["headers"],
        category["id"],
        extra_headers={"Idempotency-Key": "same-key"},
    )
    second = await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="99.99",
        extra_headers={"Idempotency-Key": "same-key"},
    )
    assert first.status_code == 201
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_idempotency_key_expires_after_24_hours_and_replays_original_response(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    key = "expire-after-24-hours"
    first = await create_expense(
        client,
        account["headers"],
        category["id"],
        extra_headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201
    original_response = first.json()

    updated = await client.patch(
        f"/api/v1/expenses/{original_response['id']}",
        json={"description": "changed after creation"},
        headers={**account["headers"], "If-Match": '"1"'},
    )
    assert updated.status_code == 200
    replay = await create_expense(
        client,
        account["headers"],
        category["id"],
        extra_headers={"Idempotency-Key": key},
    )
    assert replay.status_code == 201
    assert replay.json() == original_response

    async with SessionLocal() as session:
        await session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.user_id == uuid.UUID(account["user"]["id"]),
                IdempotencyRecord.key == key,
            )
            .values(expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        )
        await session.commit()

    retried_after_expiry = await create_expense(
        client,
        account["headers"],
        category["id"],
        extra_headers={"Idempotency-Key": key},
    )
    assert retried_after_expiry.status_code == 201
    assert retried_after_expiry.json()["id"] != original_response["id"]


@pytest.mark.asyncio
async def test_patch_version_guard_is_in_the_same_sql_update(client):
    from sqlalchemy import event

    from app.database import engine

    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    updates = []

    def capture_update(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE") and "expenses" in statement:
            updates.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture_update)
    try:
        response = await client.patch(
            f"/api/v1/expenses/{expense.json()['id']}",
            json={"description": "atomic version update"},
            headers={**account["headers"], "If-Match": '"1"'},
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_update)
    assert response.status_code == 200
    assert len(updates) == 1
    assert "version" in updates[0].lower()


@pytest.mark.asyncio
async def test_csv_import_reports_valid_and_invalid_rows(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    csv_content = (
        "category_id,amount,currency,spent_on,description\n"
        f"{category['id']},12.50,PKR,2026-10-10,valid row\n"
        f"{category['id']},0,PKR,2026-10-10,zero rejected\n"
        f"{uuid.uuid4()},10,USD,2026-10-10,foreign category\n"
    )
    response = await client.post(
        "/api/v1/expenses/import.csv",
        headers=account["headers"],
        files={"file": ("expenses.csv", csv_content.encode(), "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["imported_count"] == 1
    assert response.json()["rejected_count"] == 2
    assert {error["row"] for error in response.json()["errors"]} == {3, 4}
    items = await client.get("/api/v1/expenses", headers=account["headers"])
    assert len(items.json()["items"]) == 1


@pytest.mark.asyncio
async def test_csv_import_rejects_missing_required_headers(client):
    account = await create_account(client)
    response = await client.post(
        "/api/v1/expenses/import.csv",
        headers=account["headers"],
        files={"file": ("bad.csv", b"amount,currency\n10,USD\n", "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_csv_headers"


@pytest.mark.asyncio
async def test_fake_executable_renamed_png_is_rejected_without_storage(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    storage = AsyncMock()
    app.state.receipt_storage = storage
    response = await client.post(
        f"/api/v1/expenses/{expense.json()['id']}/receipt",
        headers=account["headers"],
        files={"file": ("invoice.png", b"MZThis is an executable", "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_receipt"
    storage.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_receipt_uses_mock_storage_and_persists_key(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"])
    expense = await create_expense(client, account["headers"], category["id"])
    storage = AsyncMock()
    app.state.receipt_storage = storage
    png = b"\x89PNG\r\n\x1a\n" + b"fake png payload"
    response = await client.post(
        f"/api/v1/expenses/{expense.json()['id']}/receipt",
        headers=account["headers"],
        files={"file": ("receipt.png", png, "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["receipt_key"].endswith(".png")
    storage.put.assert_awaited_once()
    assert storage.put.await_args.args[1:] == (png, "image/png")
