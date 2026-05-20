"""add sync jobs

Revision ID: 20260519_0002
Revises: 20260517_0001
Create Date: 2026-05-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260519_0002"
down_revision: Union[str, None] = "20260517_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.create_table(
		"sync_jobs",
		sa.Column("id", sa.Integer(), nullable=False),
		sa.Column("user_id", sa.Integer(), nullable=True),
		sa.Column("calendar_pair_id", sa.Integer(), nullable=True),
		sa.Column("friendly_name", sa.String(length=255), nullable=False),
		sa.Column("source_calendar_id", sa.Text(), nullable=False),
		sa.Column("target_calendar_id", sa.Text(), nullable=False),
		sa.Column("enabled", sa.Boolean(), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
		sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("last_status", sa.String(length=40), nullable=True),
		sa.Column("last_error", sa.Text(), nullable=True),
		sa.ForeignKeyConstraint(["calendar_pair_id"], ["calendar_pairs.id"]),
		sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
		sa.PrimaryKeyConstraint("id"),
	)


def downgrade() -> None:
	op.drop_table("sync_jobs")
