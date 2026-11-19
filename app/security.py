import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.config import get_settings
from app.errors import ApiError

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_token(
    user_id: uuid.UUID,
    token_type: str,
    token_id: uuid.UUID,
    expires_at: datetime,
) -> str:
    settings = get_settings()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type,
        "jti": str(token_id),
        "iat": datetime.now(timezone.utc),
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "jti", "type"]},
        )
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "invalid_token", "The token is invalid or expired") from exc

    if payload.get("type") != expected_type:
        raise ApiError(401, "invalid_token", "The token is invalid or expired")
    try:
        uuid.UUID(payload["sub"])
        uuid.UUID(payload["jti"])
    except (ValueError, TypeError) as exc:
        raise ApiError(401, "invalid_token", "The token is invalid or expired") from exc
    return payload


def token_expiry(token_type: str) -> datetime:
    settings = get_settings()
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_days)
    )
    return datetime.now(timezone.utc) + lifetime
