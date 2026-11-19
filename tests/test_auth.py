import uuid

import pytest

from tests.helpers import create_account


@pytest.mark.asyncio
async def test_register_creates_user_and_normalizes_email(client):
    email = f"Mixed-{uuid.uuid4()}@Example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "base_currency": "AED"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == email.lower()
    assert response.json()["base_currency"] == "AED"
    assert "password_hash" not in response.json()


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_conflict(client):
    account = await create_account(client)
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": account["email"],
            "password": "AnotherPassword123!",
            "base_currency": "PKR",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_email"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "Password123!", "base_currency": "PKR"},
        {"email": "a@example.com", "password": "short", "base_currency": "PKR"},
        {"email": "b@example.com", "password": "Password123!", "base_currency": "pkr"},
    ],
)
async def test_register_rejects_invalid_fields_with_standard_error(client, payload):
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_login_returns_access_and_refresh_tokens(client):
    account = await create_account(client)
    tokens = account["tokens"]
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 900
    assert tokens["access_token"] and tokens["refresh_token"]


@pytest.mark.asyncio
async def test_login_rejects_invalid_password(client):
    account = await create_account(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_sixth_failed_login_is_rate_limited_with_retry_after(client):
    email = f"unknown-{uuid.uuid4()}@example.com"
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong"}
        )
        assert response.status_code == 401
    sixth = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong"}
    )
    assert sixth.status_code == 429
    assert int(sixth.headers["Retry-After"]) > 0
    assert sixth.json()["error"]["code"] == "login_rate_limited"


@pytest.mark.asyncio
async def test_blocked_login_remains_blocked_until_rate_limit_expires(client):
    email = f"blocked-{uuid.uuid4()}@example.com"
    for _ in range(6):
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong"}
        )
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong"}
    )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_refresh_rotates_refresh_token(client):
    account = await create_account(client)
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": account["tokens"]["refresh_token"]},
    )
    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != account["tokens"]["refresh_token"]
    assert rotated["access_token"] != account["tokens"]["access_token"]


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_all_users_tokens(client):
    account = await create_account(client)
    second_login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": account["email"],
            "password": "CorrectHorseBattery9!",
        },
    )
    first_refresh = account["tokens"]["refresh_token"]
    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]

    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "refresh_token_reuse"

    for token in (new_refresh, second_login.json()["refresh_token"]):
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client):
    account = await create_account(client)
    response = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": account["tokens"]["refresh_token"]},
    )
    assert response.status_code == 204
    reuse = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": account["tokens"]["refresh_token"]},
    )
    assert reuse.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_requires_bearer_token(client):
    response = await client.get("/api/v1/categories")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_expired_or_malformed_access_token_is_401(client):
    response = await client.get(
        "/api/v1/categories",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
