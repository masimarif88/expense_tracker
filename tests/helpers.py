import uuid


async def create_account(client, base_currency: str = "PKR"):
    email = f"test-{uuid.uuid4()}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "CorrectHorseBattery9!",
            "base_currency": base_currency,
        },
    )
    assert response.status_code == 201, response.text
    user = response.json()
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "CorrectHorseBattery9!"},
    )
    assert login_response.status_code == 200, login_response.text
    tokens = login_response.json()
    return {
        "user": user,
        "email": email,
        "tokens": tokens,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }


async def create_category(client, headers, name="Food", budget="500.00"):
    response = await client.post(
        "/api/v1/categories",
        json={"name": name, "monthly_budget": budget},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_expense(
    client,
    headers,
    category_id,
    *,
    amount="12.34",
    currency="PKR",
    spent_on="2026-10-10",
    description="test expense",
    extra_headers=None,
):
    response = await client.post(
        "/api/v1/expenses",
        json={
            "category_id": category_id,
            "amount": amount,
            "currency": currency,
            "spent_on": spent_on,
            "description": description,
        },
        headers={**headers, **(extra_headers or {})},
    )
    return response
