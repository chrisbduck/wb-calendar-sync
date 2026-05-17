"""initial schema

Revision ID: 20260517_0001
Revises:
Create Date: 2026-05-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260517_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.create_table("users", sa.Column("id", sa.Integer(), nullable=False), sa.Column("email", sa.String(length=320), nullable=False), sa.Column("google_sub", sa.String(length=255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("email"), sa.UniqueConstraint("google_sub"))
	op.create_table("calendar_pairs", sa.Column("id", sa.Integer(), nullable=False), sa.Column("user_id", sa.Integer(), nullable=False), sa.Column("timed_calendar_id", sa.Text(), nullable=False), sa.Column("allday_calendar_id", sa.Text(), nullable=False), sa.Column("timed_sync_token", sa.Text(), nullable=True), sa.Column("allday_sync_token", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["user_id"], ["users.id"]), sa.PrimaryKeyConstraint("id"))
	op.create_table("oauth_tokens", sa.Column("id", sa.Integer(), nullable=False), sa.Column("user_id", sa.Integer(), nullable=False), sa.Column("access_token", sa.Text(), nullable=False), sa.Column("refresh_token", sa.Text(), nullable=True), sa.Column("token_uri", sa.Text(), nullable=False), sa.Column("client_id", sa.Text(), nullable=False), sa.Column("client_secret", sa.Text(), nullable=False), sa.Column("scopes", sa.Text(), nullable=False), sa.Column("expiry", sa.DateTime(timezone=True), nullable=True), sa.ForeignKeyConstraint(["user_id"], ["users.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("user_id"))
	op.create_table("conflicts", sa.Column("id", sa.Integer(), nullable=False), sa.Column("calendar_pair_id", sa.Integer(), nullable=False), sa.Column("timed_event_id", sa.Text(), nullable=True), sa.Column("allday_event_id", sa.Text(), nullable=True), sa.Column("reason", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True), sa.ForeignKeyConstraint(["calendar_pair_id"], ["calendar_pairs.id"]), sa.PrimaryKeyConstraint("id"))
	op.create_table("event_mappings", sa.Column("id", sa.Integer(), nullable=False), sa.Column("calendar_pair_id", sa.Integer(), nullable=False), sa.Column("timed_event_id", sa.Text(), nullable=False), sa.Column("allday_event_id", sa.Text(), nullable=False), sa.Column("timed_etag", sa.Text(), nullable=True), sa.Column("allday_etag", sa.Text(), nullable=True), sa.Column("last_synced_hash", sa.Text(), nullable=True), sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True), sa.Column("status", sa.String(length=40), nullable=False), sa.ForeignKeyConstraint(["calendar_pair_id"], ["calendar_pairs.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("calendar_pair_id", "allday_event_id", name="uq_mapping_allday_event"), sa.UniqueConstraint("calendar_pair_id", "timed_event_id", name="uq_mapping_timed_event"))
	op.create_table("sync_runs", sa.Column("id", sa.Integer(), nullable=False), sa.Column("calendar_pair_id", sa.Integer(), nullable=True), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True), sa.Column("status", sa.String(length=40), nullable=False), sa.Column("message", sa.Text(), nullable=True), sa.ForeignKeyConstraint(["calendar_pair_id"], ["calendar_pairs.id"]), sa.PrimaryKeyConstraint("id"))


def downgrade() -> None:
	op.drop_table("sync_runs")
	op.drop_table("event_mappings")
	op.drop_table("conflicts")
	op.drop_table("oauth_tokens")
	op.drop_table("calendar_pairs")
	op.drop_table("users")
