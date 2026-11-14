"""add client_key (hashed caller IP) to analyses and datasets, for per-visitor daily limits

Revision ID: b81f3d6a2c47
Revises: a7d2c9e41b05
Create Date: 2026-11-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b81f3d6a2c47'
down_revision: Union[str, Sequence[str], None] = 'a7d2c9e41b05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("analyses", "datasets"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("client_key", sa.String(length=64), nullable=True))
            batch_op.create_index(f"ix_{table}_client_key", ["client_key"])


def downgrade() -> None:
    for table in ("analyses", "datasets"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_client_key")
            batch_op.drop_column("client_key")
