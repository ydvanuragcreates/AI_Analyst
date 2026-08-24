"""
Phase 9 production-readiness audit -- JWT-specific security tests, not
already covered by the general auth tests in test_v1_api.py:
expiration, algorithm restriction (no "alg: none"/confusion attacks),
and tampered-signature rejection.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import InvalidTokenError, create_access_token, decode_access_token


def test_expired_token_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expired_token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(expired_token)


def test_token_signed_with_a_different_secret_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=60)},
        "a-completely-different-secret-the-attacker-knows",
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(forged)


def test_algorithm_none_token_is_rejected() -> None:
    """The classic JWT "alg: none" attack -- a token that claims no
    signature is required at all. PyJWT's `algorithms=[settings.jwt_algorithm]`
    allowlist (see core/security.py::decode_access_token) is what closes
    this; without an explicit allowlist, some JWT libraries would honor
    whatever `alg` the token itself claims."""

    import base64
    import json

    def _b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    now = datetime.now(timezone.utc)
    header = {"alg": "none", "typ": "JWT"}
    payload = {"sub": str(uuid.uuid4()), "iat": now.timestamp(), "exp": (now + timedelta(minutes=60)).timestamp()}
    # A hand-built, deliberately unsigned token (empty signature segment)
    # -- exactly the shape an "alg: none" attack sends.
    unsigned_token = f"{_b64(header)}.{_b64(payload)}."

    with pytest.raises(InvalidTokenError):
        decode_access_token(unsigned_token)


def test_algorithm_confusion_hs256_token_verified_as_different_algorithm_is_rejected() -> None:
    """A token legitimately signed with HS256 must not be accepted if the
    server were ever misconfigured to expect a different algorithm --
    decode_access_token pins to settings.jwt_algorithm exactly, so a
    token claiming a different `alg` in its header is rejected outright."""

    settings = get_settings()
    assert settings.jwt_algorithm == "HS256"  # this test's premise

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=60)},
        settings.jwt_secret_key,
        algorithm="HS512",  # different algorithm than configured
    )

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_valid_token_round_trips() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_token_with_no_subject_claim_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode({"iat": now, "exp": now + timedelta(minutes=60)}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(InvalidTokenError):
        decode_access_token(token)
