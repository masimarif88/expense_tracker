import pytest

from tests.helpers import create_account, create_category, create_expense


@pytest.mark.asyncio
async def test_monthly_report_converts_mixed_currencies_and_uses_prior_rate(client):
    account = await create_account(client, base_currency="PKR")
    category = await create_category(
        client, account["headers"], name="Travel", budget="6000.00"
    )
    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="10.00",
        currency="USD",
        spent_on="2026-09-10",
        description="September USD",
    )
    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="10.00",
        currency="USD",
        spent_on="2026-10-10",
        description="October USD",
    )
    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="8.00",
        currency="EUR",
        spent_on="2026-10-10",
        description="October EUR uses September rate",
    )

    response = await client.get(
        "/api/v1/reports/monthly?year=2026&month=10",
        headers=account["headers"],
    )
    assert response.status_code == 200
    report = response.json()
    assert report["base_currency"] == "PKR"
    result = report["categories"][0]
    assert result["total"] == "5800.00"
    assert result["budget"] == "6000.00"
    assert result["percent_used"] == "96.67"
    assert result["previous_month_total"] == "2800.00"
    assert result["change_amount"] == "3000.00"
    assert result["change_percent"] == "107.14"


@pytest.mark.asyncio
async def test_monthly_report_errors_if_currency_has_no_rate_before_expense(client):
    account = await create_account(client, base_currency="PKR")
    category = await create_category(client, account["headers"])
    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="10.00",
        currency="GBP",
        spent_on="2026-10-10",
    )
    response = await client.get(
        "/api/v1/reports/monthly?year=2026&month=10",
        headers=account["headers"],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "exchange_rate_missing"


@pytest.mark.asyncio
async def test_monthly_report_for_month_without_expenses_returns_zero(client):
    account = await create_account(client, base_currency="AED")
    category = await create_category(client, account["headers"])
    response = await client.get(
        "/api/v1/reports/monthly?year=2026&month=10",
        headers=account["headers"],
    )
    assert response.status_code == 200
    result = response.json()["categories"][0]
    assert result["category_id"] == category["id"]
    assert result["total"] == "0.00"
    assert result["previous_month_total"] == "0.00"
    assert result["change_percent"] is None


@pytest.mark.asyncio
async def test_monthly_report_validates_month_and_year(client):
    account = await create_account(client)
    invalid_month = await client.get(
        "/api/v1/reports/monthly?year=2026&month=13",
        headers=account["headers"],
    )
    invalid_year = await client.get(
        "/api/v1/reports/monthly?year=1900&month=10",
        headers=account["headers"],
    )
    assert invalid_month.status_code == 422
    assert invalid_year.status_code == 422


@pytest.mark.asyncio
async def test_category_list_converts_month_spend_to_base_currency(client):
    account = await create_account(client, base_currency="PKR")
    category = await create_category(
        client, account["headers"], budget="3000.00"
    )
    from datetime import date

    await create_expense(
        client,
        account["headers"],
        category["id"],
        amount="10.00",
        currency="USD",
        spent_on=date.today().isoformat(),
    )
    response = await client.get("/api/v1/categories", headers=account["headers"])
    assert response.status_code == 200
    item = response.json()[0]
    assert item["monthly_spend"] == "2780.00"
    assert item["budget_percent_used"] == "92.67"


@pytest.mark.asyncio
async def test_category_duplicate_and_invalid_budget(client):
    account = await create_account(client)
    category = await create_category(client, account["headers"], name="Bills")
    duplicate = await client.post(
        "/api/v1/categories",
        json={"name": "Bills"},
        headers=account["headers"],
    )
    invalid = await client.post(
        "/api/v1/categories",
        json={"name": "Invalid", "monthly_budget": "-1"},
        headers=account["headers"],
    )
    assert category["name"] == "Bills"
    assert duplicate.status_code == 409
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_category_list_returns_404_for_foreign_category_filter(client):
    owner = await create_account(client)
    other = await create_account(client)
    category = await create_category(client, owner["headers"])
    response = await client.get(
        f"/api/v1/expenses?category_id={category['id']}",
        headers=other["headers"],
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "category_not_found"
