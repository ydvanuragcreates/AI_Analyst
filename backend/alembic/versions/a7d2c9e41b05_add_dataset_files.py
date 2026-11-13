"""add dataset_files: original upload bytes, for restoring datasets on ephemeral hosts

Revision ID: a7d2c9e41b05
Revises: c391ffe21c7a
Create Date: 2026-11-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.base


revision: str = 'a7d2c9e41b05'
down_revision: Union[str, Sequence[str], None] = 'c391ffe21c7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_files",
        sa.Column("dataset_id", app.db.base.GUID(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id"),
    )


def downgrade() -> None:
    op.drop_table("dataset_files")
