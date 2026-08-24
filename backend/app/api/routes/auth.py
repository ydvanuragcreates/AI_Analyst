"""
Authentication endpoints: register, login, me.

Routes stay thin (same convention as every other route module in this
codebase) -- password hashing/verification and JWT creation live in
core/security.py, DB access lives in UserRepository.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError, ValidationError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models.user import User
from app.db.repositories.user_repository import UserRepository
from app.db.session import get_db_session
from app.middleware.rate_limit import rate_limit
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=201, dependencies=[Depends(rate_limit("auth", "auth_rate_limit"))])
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db_session), settings: Settings = Depends(get_settings)) -> TokenResponse:
    repo = UserRepository(session)
    if await repo.get_by_email(payload.email) is not None:
        # Same message shape as "wrong password" at login -- avoids
        # confirming an email doesn't already exist either way, but here
        # the account genuinely can't be created, so 422 (not 401) is
        # the honest status; the *message* still doesn't leak more than
        # "this address can't be registered."
        raise ValidationError("This email address cannot be registered.")

    user = await repo.create(email=payload.email, password_hash=hash_password(payload.password))
    await session.commit()

    logger.info("User registered", extra={"user_id": str(user.id)})
    token = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_in_minutes=settings.access_token_expire_minutes)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit("auth", "auth_rate_limit"))])
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session), settings: Settings = Depends(get_settings)) -> TokenResponse:
    repo = UserRepository(session)
    user = await repo.get_by_email(payload.email)

    # Never distinguish "no such user" from "wrong password" -- both take
    # the same code path to the same error, so a caller can't enumerate
    # registered emails by timing/response differences alone.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthenticationError("Incorrect email or password")

    logger.info("User logged in", extra={"user_id": str(user.id)})
    token = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_in_minutes=settings.access_token_expire_minutes)


@router.get("/me", response_model=UserResponse)
async def read_current_user(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=current_user.id, email=current_user.email, created_at=current_user.created_at)
