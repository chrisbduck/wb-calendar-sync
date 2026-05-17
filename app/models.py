from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow():
	return datetime.now(timezone.utc)


class User(Base):
	__tablename__ = "users"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
	google_sub: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
	created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

	tokens = relationship("OAuthToken", back_populates="user", cascade="all, delete-orphan")
	calendar_pairs = relationship("CalendarPair", back_populates="user", cascade="all, delete-orphan")


class OAuthToken(Base):
	__tablename__ = "oauth_tokens"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
	access_token: Mapped[str] = mapped_column(Text, nullable=False)
	refresh_token: Mapped[str | None] = mapped_column(Text)
	token_uri: Mapped[str] = mapped_column(Text, nullable=False)
	client_id: Mapped[str] = mapped_column(Text, nullable=False)
	client_secret: Mapped[str] = mapped_column(Text, nullable=False)
	scopes: Mapped[str] = mapped_column(Text, nullable=False)
	expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

	user = relationship("User", back_populates="tokens")


class CalendarPair(Base):
	__tablename__ = "calendar_pairs"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
	timed_calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
	allday_calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
	timed_sync_token: Mapped[str | None] = mapped_column(Text)
	allday_sync_token: Mapped[str | None] = mapped_column(Text)
	created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
	updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

	user = relationship("User", back_populates="calendar_pairs")
	event_mappings = relationship("EventMapping", back_populates="calendar_pair", cascade="all, delete-orphan")
	sync_runs = relationship("SyncRun", back_populates="calendar_pair", cascade="all, delete-orphan")
	conflicts = relationship("Conflict", back_populates="calendar_pair", cascade="all, delete-orphan")


class EventMapping(Base):
	__tablename__ = "event_mappings"
	__table_args__ = (
		UniqueConstraint("calendar_pair_id", "timed_event_id", name="uq_mapping_timed_event"),
		UniqueConstraint("calendar_pair_id", "allday_event_id", name="uq_mapping_allday_event"),
	)

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	calendar_pair_id: Mapped[int] = mapped_column(ForeignKey("calendar_pairs.id"), nullable=False)
	timed_event_id: Mapped[str] = mapped_column(Text, nullable=False)
	allday_event_id: Mapped[str] = mapped_column(Text, nullable=False)
	timed_etag: Mapped[str | None] = mapped_column(Text)
	allday_etag: Mapped[str | None] = mapped_column(Text)
	last_synced_hash: Mapped[str | None] = mapped_column(Text)
	last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
	status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)

	calendar_pair = relationship("CalendarPair", back_populates="event_mappings")


class SyncRun(Base):
	__tablename__ = "sync_runs"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	calendar_pair_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_pairs.id"))
	started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
	finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
	status: Mapped[str] = mapped_column(String(40), nullable=False, default="running")
	message: Mapped[str | None] = mapped_column(Text)

	calendar_pair = relationship("CalendarPair", back_populates="sync_runs")


class Conflict(Base):
	__tablename__ = "conflicts"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	calendar_pair_id: Mapped[int] = mapped_column(ForeignKey("calendar_pairs.id"), nullable=False)
	timed_event_id: Mapped[str | None] = mapped_column(Text)
	allday_event_id: Mapped[str | None] = mapped_column(Text)
	reason: Mapped[str] = mapped_column(Text, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
	resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

	calendar_pair = relationship("CalendarPair", back_populates="conflicts")
