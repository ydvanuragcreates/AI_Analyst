"""
SQLAlchemy declarative base, plus the portable types every model uses.

Two constraints shaped this file: (1) production runs on PostgreSQL, but
(2) the test suite runs against SQLite (see tests/conftest.py) rather than
requiring a real Postgres server -- there is no Docker/Postgres available
in this environment, so "tested" for the DB layer means "tested against
SQLite, verified correct-by-inspection against Postgres." GUID is the
standard SQLAlchemy recipe for a type that's a real `uuid` column on
Postgres and a CHAR(32) hex string everywhere else, so the same model
definitions and Alembic migration work unchanged on both.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CHAR, DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Declarative base every Phase 9 model inherits from."""


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID type when the dialect is postgresql,
    otherwise stores as a 32-character hex string (CHAR(32)) -- e.g. on
    SQLite in tests. Always works with Python `uuid.UUID` objects at the
    application layer regardless of which one is underneath.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    """created_at / updated_at, set and maintained by the application
    (not DB-side triggers) -- consistent with `utcnow()` being the same
    helper used for every other timestamp in this codebase."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
