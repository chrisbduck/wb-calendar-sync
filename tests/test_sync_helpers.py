import unittest
from datetime import datetime, timedelta, timezone

from app.future_sync import parse_allday_title_for_timed_event
from app.google_client import convert_expiry_for_database, convert_expiry_for_google, missing_required_scopes
from app.db import db_session
from app.models import Conflict, EventMapping
from app.routes import calendar_options, serialize_datetime
from app.sync import ALLDAY_TO_TIMED, TIMED_TO_ALLDAY, allday_event_to_timed_calendar_event, event_starts_before_sync_cutoff, is_sync_generated_from, query_start_for_overlapping_events, sync_mapped_pair_from_allday, sync_mapped_pair_from_timed, timed_event_to_allday_event


class FakeCalendarService:
	def __init__(self, calendars):
		self.calendars = calendars
		self.update_count = 0
		self.insert_count = 0

	def events(self):
		return self

	def get(self, calendarId, eventId):
		return FakeRequest(lambda: self.calendars[calendarId][eventId])

	def update(self, calendarId, eventId, body):
		def execute():
			self.update_count += 1
			current = self.calendars[calendarId][eventId]
			updated = {**current, **body, "id": eventId, "etag": f"{current.get('etag', 'etag')}-u{self.update_count}", "created": current.get("created"), "status": current.get("status", "confirmed")}
			self.calendars[calendarId][eventId] = updated
			return updated
		return FakeRequest(execute)

	def insert(self, calendarId, body):
		def execute():
			self.insert_count += 1
			event_id = f"created-{self.insert_count}"
			created = {**body, "id": event_id, "etag": f"inserted-{self.insert_count}", "created": "2026-05-17T12:00:00Z", "status": "confirmed"}
			self.calendars[calendarId][event_id] = created
			return created
		return FakeRequest(execute)


class FakeRequest:
	def __init__(self, execute):
		self.execute = execute


class SyncHelperTests(unittest.TestCase):
	def test_timed_event_to_allday_event(self):
		event = {"id": "abc123", "summary": "Doctor", "description": "Bring forms", "location": "Clinic", "start": {"dateTime": "2026-05-17T14:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T15:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		result = timed_event_to_allday_event(event, "timed@example.com")
		self.assertEqual(result["summary"], "2pm Doctor")
		self.assertEqual(result["start"], {"date": "2026-05-17"})
		self.assertEqual(result["end"], {"date": "2026-05-18"})
		self.assertEqual(result["location"], "Clinic")
		self.assertEqual(result["description"], "Bring forms")
		self.assertEqual(result["extendedProperties"]["private"]["sourceEventId"], "abc123")
		self.assertEqual(result["extendedProperties"]["private"]["syncDirection"], TIMED_TO_ALLDAY)

	def test_allday_title_parser_finds_single_times_and_ranges(self):
		self.assertEqual(parse_allday_title_for_timed_event("2:30pm Doctor"), {"hour": 14, "minute": 30, "summary": "Doctor", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("14:00 Doctor"), {"hour": 14, "minute": 0, "summary": "Doctor", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("Dinner 5-7pm"), {"hour": 17, "minute": 0, "summary": "Dinner", "duration_minutes": 120})
		self.assertEqual(parse_allday_title_for_timed_event("5pm to 7pm Dinner"), {"hour": 17, "minute": 0, "summary": "Dinner", "duration_minutes": 120})
		self.assertIsNone(parse_allday_title_for_timed_event("Doctor at 2"))

	def test_allday_event_to_timed_event_uses_time_when_clear(self):
		event = {"id": "daily1", "summary": "Dinner 5-7pm", "description": "Bring salad", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles")
		self.assertEqual(result["summary"], "Dinner")
		self.assertEqual(result["start"], {"dateTime": "2026-05-17T17:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(result["end"], {"dateTime": "2026-05-17T19:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(result["description"], "Bring salad")
		self.assertEqual(result["extendedProperties"]["private"]["syncDirection"], ALLDAY_TO_TIMED)

	def test_allday_rename_without_time_keeps_existing_hourly_time(self):
		event = {"id": "daily1", "summary": "Appointment2", "description": "New notes", "location": "Clinic", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		existing = {"start": {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00", "timeZone": "America/Los_Angeles"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles", existing)
		self.assertEqual(result["summary"], "Appointment2")
		self.assertEqual(result["start"], existing["start"])
		self.assertEqual(result["end"], existing["end"])
		self.assertEqual(result["description"], "New notes")
		self.assertEqual(result["location"], "Clinic")

	def test_allday_event_to_timed_event_falls_back_to_allday_without_time(self):
		event = {"id": "daily2", "summary": "Vacation", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-20"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles")
		self.assertEqual(result["summary"], "Vacation")
		self.assertEqual(result["start"], {"date": "2026-05-17"})
		self.assertEqual(result["end"], {"date": "2026-05-20"})

	def test_sync_generated_detection_checks_direction_and_source(self):
		event = {"extendedProperties": {"private": {"calendarSyncApp": "true", "syncDirection": ALLDAY_TO_TIMED, "sourceCalendarId": "allday@example.com"}}}
		self.assertTrue(is_sync_generated_from(event, "allday@example.com", ALLDAY_TO_TIMED))
		self.assertFalse(is_sync_generated_from(event, "timed@example.com", ALLDAY_TO_TIMED))
		self.assertFalse(is_sync_generated_from(event, "allday@example.com", TIMED_TO_ALLDAY))

	def test_event_starts_before_sync_cutoff(self):
		cutoff = query_start_for_overlapping_events("America/Los_Angeles").date()
		before_cutoff = cutoff - timedelta(days=1)
		after_cutoff = cutoff + timedelta(days=1)
		self.assertTrue(event_starts_before_sync_cutoff({"start": {"date": before_cutoff.isoformat()}, "end": {"date": after_cutoff.isoformat()}}, "America/Los_Angeles"))
		self.assertFalse(event_starts_before_sync_cutoff({"start": {"date": cutoff.isoformat()}}, "America/Los_Angeles"))
		self.assertFalse(event_starts_before_sync_cutoff({"start": {"date": after_cutoff.isoformat()}}, "America/Los_Angeles"))

	def test_missing_required_scopes(self):
		granted = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
		self.assertEqual(missing_required_scopes(granted), ["https://www.googleapis.com/auth/calendar"])

	def test_google_credential_expiry_uses_naive_utc(self):
		aware = datetime(2026, 5, 18, 12, 30, tzinfo=timezone.utc)
		self.assertEqual(convert_expiry_for_google(aware), datetime(2026, 5, 18, 12, 30))

	def test_database_expiry_uses_aware_utc(self):
		naive = datetime(2026, 5, 18, 12, 30)
		self.assertEqual(convert_expiry_for_database(naive), datetime(2026, 5, 18, 12, 30, tzinfo=timezone.utc))

	def test_calendar_options_only_show_ids_for_duplicate_names(self):
		calendars = [{"id": "one@example.com", "summary": "Family"}, {"id": "two@example.com", "summary": "Family"}, {"id": "three@example.com", "summary": "Work"}]
		options = calendar_options(calendars)
		self.assertEqual(options[0]["label"], "Family — one@example.com")
		self.assertEqual(options[1]["label"], "Family — two@example.com")
		self.assertEqual(options[2]["label"], "Work")

	def test_serialize_datetime_marks_naive_values_as_utc(self):
		self.assertEqual(serialize_datetime(datetime(2026, 5, 17, 20, 30)), "2026-05-17T20:30:00Z")

	def test_mapped_daily_rename_updates_hourly_event(self):
		timed = {"id": "timed1", "etag": "t1", "created": "2026-05-17T09:00:00Z", "summary": "Appointment", "start": {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00", "timeZone": "America/Los_Angeles"}}
		allday = {"id": "daily1", "etag": "a2", "created": "2026-05-17T09:01:00Z", "summary": "9am Appointment2", "description": "Updated", "location": "Clinic", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}})
		pair = type("Pair", (), {"id": 1, "timed_calendar_id": "timed-cal", "allday_calendar_id": "daily-cal"})()
		mapping = EventMapping(calendar_pair_id=1, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=TIMED_TO_ALLDAY)
		self.assertEqual(sync_mapped_pair_from_allday(service, pair, mapping, allday, "America/Los_Angeles"), "updated")
		updated = service.calendars["timed-cal"]["timed1"]
		self.assertEqual(updated["summary"], "Appointment2")
		self.assertEqual(updated["description"], "Updated")
		self.assertEqual(updated["location"], "Clinic")
		self.assertEqual(updated["start"], {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"})

	def test_mapped_hourly_rename_updates_daily_event(self):
		timed = {"id": "timed1", "etag": "t2", "created": "2026-05-17T09:00:00Z", "summary": "Appointment3", "description": "", "location": "", "start": {"dateTime": "2026-05-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		allday = {"id": "daily1", "etag": "a1", "created": "2026-05-17T09:01:00Z", "summary": "9am Appointment", "description": "Old", "location": "Office", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}})
		pair = type("Pair", (), {"id": 1, "timed_calendar_id": "timed-cal", "allday_calendar_id": "daily-cal"})()
		mapping = EventMapping(calendar_pair_id=1, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=TIMED_TO_ALLDAY)
		self.assertEqual(sync_mapped_pair_from_timed(service, pair, mapping, timed, "America/Los_Angeles"), "updated")
		updated = service.calendars["daily-cal"]["daily1"]
		self.assertEqual(updated["summary"], "9am Appointment3")
		self.assertEqual(updated["description"], "")
		self.assertEqual(updated["location"], "")

	def test_both_sides_changed_earlier_created_event_wins_and_records_conflict(self):
		db_session.rollback()
		timed = {"id": "timed1", "etag": "t2", "created": "2026-05-17T09:00:00Z", "summary": "Original wins", "start": {"dateTime": "2026-05-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		allday = {"id": "daily1", "etag": "a2", "created": "2026-05-17T09:01:00Z", "summary": "9am Later edit", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}})
		pair = type("Pair", (), {"id": 1, "timed_calendar_id": "timed-cal", "allday_calendar_id": "daily-cal"})()
		mapping = EventMapping(calendar_pair_id=1, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=TIMED_TO_ALLDAY)
		try:
			self.assertEqual(sync_mapped_pair_from_allday(service, pair, mapping, allday, "America/Los_Angeles"), "updated")
			self.assertEqual(service.calendars["daily-cal"]["daily1"]["summary"], "9am Original wins")
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()


if __name__ == "__main__":
	unittest.main()
