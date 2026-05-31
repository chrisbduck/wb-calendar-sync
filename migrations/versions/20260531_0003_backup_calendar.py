"""add backup calendar

Revision ID: 20260531_0003
Revises: 20260519_0002
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260531_0003"
down_revision: Union[str, None] = "20260519_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.add_column("calendar_pairs", sa.Column("backup_calendar_id", sa.Text(), nullable=True))
	op.add_column("sync_jobs", sa.Column("backup_calendar_id", sa.Text(), nullable=True))
	op.execute("DELETE FROM sync_jobs")


def downgrade() -> None:
	op.drop_column("sync_jobs", "backup_calendar_id")
	op.drop_column("calendar_pairs", "backup_calendar_id")
