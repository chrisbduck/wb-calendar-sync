import unittest

from app.future_sync import parse_allday_title_for_timed_event
from app.google_client import missing_required_scopes
from app.routes import calendar_options
from app.sync import ALLDAY_TO_TIMED, TIMED_TO_ALLDAY, allday_event_to_timed_calendar_event, is_sync_generated_from, timed_event_to_allday_event


class SyncHelperTests(unittest.TestCase):
	def test_timed_event_to_allday_event(self):
		event = {"id": "abc123", "summary": "Doctor", "description": "Bring forms", "location": "Clinic", "start": {"dateTime": "2026-05-17T14:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T15:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		result = timed_event_to_allday_event(event, "timed@example.com")
		self.assertEqual(result["summary"], "2pm Doctor")
		self.assertEqual(result["start"], {"date": "2026-05-17"})
		self.assertEqual(result["end"], {"date": "2026-05-18"})
		self.assertEqual(result["location"], "Clinic")
		self.assertIn("Original event ID: abc123", result["description"])
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
		self.assertIn("Original event ID: daily1", result["description"])
		self.assertEqual(result["extendedProperties"]["private"]["syncDirection"], ALLDAY_TO_TIMED)

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

	def test_missing_required_scopes(self):
		granted = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
		self.assertEqual(missing_required_scopes(granted), ["https://www.googleapis.com/auth/calendar"])

	def test_calendar_options_only_show_ids_for_duplicate_names(self):
		calendars = [{"id": "one@example.com", "summary": "Family"}, {"id": "two@example.com", "summary": "Family"}, {"id": "three@example.com", "summary": "Work"}]
		options = calendar_options(calendars)
		self.assertEqual(options[0]["label"], "Family — one@example.com")
		self.assertEqual(options[1]["label"], "Family — two@example.com")
		self.assertEqual(options[2]["label"], "Work")


if __name__ == "__main__":
	unittest.main()
