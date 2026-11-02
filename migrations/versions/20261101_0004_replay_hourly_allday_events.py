"""Replay the Hourly sync window to recover previously skipped all-day events.

Revision ID: 20261101_0004
Revises: 20260531_0003
"""
from alembic import op


revision = "20261101_0004"
down_revision = "20260531_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
	# Keep mappings so the next full Hourly listing only creates missing mirrors.
	op.execute("UPDATE calendar_pairs SET timed_sync_token = NULL")


def downgrade() -> None:
	# Google issues replacement cursors on the next successful sync.
	pass
