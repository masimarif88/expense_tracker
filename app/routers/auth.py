import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import SessionDep
from app.errors import ApiError
from app.models import RefreshToken, User
from app.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenPair,
    TokenRequest,
    UserOut,
)
from app.security import (
    create_token,
    decode_token,
    hash_password,
    token_expiry,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

INCREMENT_FAILURE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {count, redis.call('TTL', KEYS[1])}
"""


async def _new_token_pair(
    session: AsyncSession, user: User
) -> TokenPair:
    refresh_id = uuid.uuid4()
    refresh_expires = token_expiry("refresh")
    session.add(
        RefreshToken(
            id=refresh_id,
            user_id=user.id,
            expires_at=refresh_expires,
        )
    )
    access_expires = token_expiry("access")
    return TokenPair(
        access_token=create_token(user.id, "access", uuid.uuid4(), access_expires),
        refresh_token=create_token(user.id, "refresh", refresh_id, refresh_expires),
        expires_in=get_settings().access_token_minutes * 60,
    )


def _redis(request: Request):
    return request.app.state.redis


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: SessionDep) -> User:
    email = str(payload.email).lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise ApiError(409, "duplicate_email", "An account with this email already exists")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        base_currency=payload.base_currency,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "users_email_key":
            raise ApiError(
                409, "duplicate_email", "An account with this email already exists"
            ) from exc
        raise
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest, request: Request, session: SessionDep
) -> TokenPair:
    email = str(payload.email).lower()
    settings = get_settings()
    client = _redis(request)
    key = f"auth:login-failures:{email}"

    try:
        attempts = await client.get(key)
        if attempts is not None and int(attempts) >= settings.login_failure_limit:
            ttl = await client.ttl(key)
            retry_after = max(ttl, 1)
            raise ApiError(
                429,
                "login_rate_limited",
                "Too many failed login attempts",
                headers={"Retry-After": str(retry_after)},
            )
    except RedisError as exc:
        raise ApiError(503, "rate_limit_unavailable", "Login rate limiting is unavailable") from exc

    user = await session.scalar(select(User).where(User.email == email))
    password_matches = (
        verify_password(payload.password, user.password_hash) if user is not None else False
    )
    if user is None or not password_matches:
        try:
            count, ttl = await client.eval(
                INCREMENT_FAILURE_SCRIPT,
                1,
                key,
                settings.login_failure_window_seconds,
            )
        except RedisError as exc:
            raise ApiError(
                503, "rate_limit_unavailable", "Login rate limiting is unavailable"
            ) from exc
        if int(count) > settings.login_failure_limit:
            raise ApiError(
                429,
                "login_rate_limited",
                "Too many failed login attempts",
                headers={"Retry-After": str(max(int(ttl), 1))},
            )
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")

    try:
        await client.delete(key)
    except RedisError as exc:
        raise ApiError(503, "rate_limit_unavailable", "Login rate limiting is unavailable") from exc

    pair = await _new_token_pair(session, user)
    await session.commit()
    return pair


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: TokenRequest, session: SessionDep
) -> TokenPair:
    claims = decode_token(payload.refresh_token, "refresh")
    user_id = uuid.UUID(claims["sub"])
    refresh_id = uuid.UUID(claims["jti"])
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.id == refresh_id).with_for_update()
    )
    if token is None or token.user_id != user_id:
        raise ApiError(401, "invalid_token", "The refresh token is invalid")

    now = datetime.now(timezone.utc)
    if token.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.commit()
        raise ApiError(401, "refresh_token_reuse", "Refresh-token reuse detected; all sessions were revoked")
    if token.expires_at <= now:
        raise ApiError(401, "invalid_token", "The refresh token is invalid or expired")

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(401, "invalid_token", "The refresh token is invalid")

    pair = await _new_token_pair(session, user)
    token.revoked_at = now
    token.replaced_by = uuid.UUID(decode_token(pair.refresh_token, "refresh")["jti"])
    await session.commit()
    return pair


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: TokenRequest, session: SessionDep) -> Response:
    claims = decode_token(payload.refresh_token, "refresh")
    refresh_id = uuid.UUID(claims["jti"])
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.id == refresh_id).with_for_update()
    )
    if token is None or str(token.user_id) != claims["sub"]:
        raise ApiError(401, "invalid_token", "The refresh token is invalid")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(timezone.utc)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
