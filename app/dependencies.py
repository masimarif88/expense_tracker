import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import ApiError
from app.models import User
from app.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    session: SessionDep,
) -> User:
    if credentials is None:
        raise ApiError(401, "authentication_required", "A valid access token is required")
    payload = decode_token(credentials.credentials, "access")
    user_id = uuid.UUID(payload["sub"])
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(401, "invalid_token", "The token is invalid or expired")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
