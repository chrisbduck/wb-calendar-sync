import unittest

from app.future_sync import parse_allday_title_for_timed_event
from app.google_client import missing_required_scopes
from app.sync import timed_event_to_allday_event


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

	def test_future_title_parser_is_strict(self):
		self.assertEqual(parse_allday_title_for_timed_event("2:30pm Doctor"), {"hour": 14, "minute": 30, "summary": "Doctor", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("14:00 Doctor"), {"hour": 14, "minute": 0, "summary": "Doctor", "duration_minutes": 60})
		self.assertIsNone(parse_allday_title_for_timed_event("Doctor at 2"))

	def test_missing_required_scopes(self):
		granted = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
		self.assertEqual(missing_required_scopes(granted), ["https://www.googleapis.com/auth/calendar"])


if __name__ == "__main__":
	unittest.main()
