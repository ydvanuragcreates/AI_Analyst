"""
Shared FastAPI dependencies for the Phase 9 v1 API: the authenticated
user and a couple of small request-validation helpers used across
several v1 routes.
"""

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ValidationError
from app.core.security import InvalidTokenError, decode_access_token
from app.db.models.user import User
from app.db.repositories.user_repository import UserRepository
from app.db.session import get_db_session

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the caller's User from the `Authorization: Bearer <token>`
    header, or raise AuthenticationError (401).

    Also stamps `request.state.user_id` -- middleware/rate_limit.py reads
    this to key rate limits per-user instead of per-IP on authenticated
    routes, without this dependency needing to know anything about rate
    limiting itself.
    """

    if credentials is None:
        raise AuthenticationError("Missing Authorization header")

    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise AuthenticationError(f"Invalid or expired token: {exc}") from None

    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise AuthenticationError("User no longer exists")

    request.state.user_id = str(user.id)
    return user


class PageParams:
    """Validated pagination: page >= 1, 1 <= page_size <= Settings.max_page_size."""

    def __init__(self, page: int = 1, page_size: int | None = None) -> None:
        from app.core.config import get_settings

        settings = get_settings()
        if page < 1:
            raise ValidationError("page must be >= 1")
        page_size = page_size if page_size is not None else settings.default_page_size
        if not (1 <= page_size <= settings.max_page_size):
            raise ValidationError(f"page_size must be between 1 and {settings.max_page_size}")
        self.page = page
        self.page_size = page_size
