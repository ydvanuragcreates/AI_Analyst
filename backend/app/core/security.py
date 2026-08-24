"""
Password hashing and JWT access tokens.

Centralized here so nothing else in the app touches `bcrypt`/`jwt`
directly -- same "one place owns this concern" pattern as core/llm.py.
Passwords are never stored or logged in plaintext; only `password_hash`
(a bcrypt hash) ever reaches the database (see db/models/user.py).

Uses the `bcrypt` library directly rather than `passlib` -- passlib's
bcrypt backend is incompatible with bcrypt>=4.1 (it probes
`bcrypt.__about__.__version__`, removed upstream) and passlib itself is
unmaintained; `bcrypt` alone is simpler and has no such issue.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import Settings, get_settings

# bcrypt silently truncates/errors past 72 bytes; hashing is only ever
# reached after Pydantic's own password-length validation (schemas/auth.py),
# so this is a defensive backstop, not the primary length check.
_MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    truncated = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    truncated = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    try:
        return bcrypt.checkpw(truncated, password_hash.encode("utf-8"))
    except ValueError:
        return False


class InvalidTokenError(Exception):
    """The access token is missing, malformed, expired, or forged."""


def create_access_token(user_id: uuid.UUID, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings | None = None) -> uuid.UUID:
    """Return the authenticated user's id, or raise InvalidTokenError.

    A single, narrow return type (just the user id) keeps every caller
    (the `get_current_user` dependency) from having to re-validate the
    payload shape itself.
    """

    settings = settings or get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    subject = payload.get("sub")
    if not subject:
        raise InvalidTokenError("Token has no subject")
    try:
        return uuid.UUID(subject)
    except ValueError as exc:
        raise InvalidTokenError("Token subject is not a valid user id") from exc
