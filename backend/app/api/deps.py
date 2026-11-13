"""
Shared FastAPI dependencies for the v1 API: the (single, built-in) local
user and a couple of small request-validation helpers used across several
v1 routes.

Authentication has been removed. Datasets and analyses are still stored
with a `user_id` (the schema and every repository's ownership filter are
unchanged), but that id now always belongs to one automatically-created
local user rather than to whoever logged in.
"""

from fastapi import Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.db.models.user import User
from app.db.repositories.user_repository import UserRepository
from app.db.session import get_db_session

LOCAL_USER_EMAIL = "local@localhost"


async def get_current_user(session: AsyncSession = Depends(get_db_session)) -> User:
    """Return the built-in local user, creating it on first use.

    If the database already has users (from before authentication was
    removed), the oldest one is reused so its existing datasets and
    analyses stay visible. No credentials are read. Rate limiting falls back to the client IP
    because `request.state.user_id` is intentionally not set here (every
    caller shares this one user, so a per-user key would be a single
    global bucket).
    """

    repo = UserRepository(session)
    user = await repo.get_oldest()
    if user is not None:
        return user

    try:
        # No password: nothing ever logs in as this user.
        user = await repo.create(email=LOCAL_USER_EMAIL, password_hash="")
        await session.commit()
    except IntegrityError:
        # Two first-ever requests raced to create it; the other one won.
        await session.rollback()
        user = await repo.get_oldest()
        assert user is not None
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
