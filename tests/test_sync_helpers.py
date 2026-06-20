import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.future_sync import parse_allday_title_for_timed_event
from app.google_client import GOOGLE_RECONNECT_MESSAGE, GoogleReconnectRequiredError, RefreshError, convert_expiry_for_database, convert_expiry_for_google, credentials_from_token, missing_required_scopes
from app.db import db_session
from app.models import CalendarPair, Conflict, EventMapping, OAuthToken, SyncJob
from app.routes import calendar_options, serialize_datetime, validate_calendar_setup
from app.sync import ALLDAY_TO_TIMED, TIMED_TO_ALLDAY, SyncSetupRequiredError, allday_event_to_timed_calendar_event, clear_deleted_event_mappings, event_starts_before_sync_cutoff, is_sync_generated_from, parse_google_datetime, query_start_for_overlapping_events, run_sync_for_pair, sync_allday_event, sync_mapped_pair_from_allday, sync_mapped_pair_from_timed, sync_timed_event, timed_event_to_allday_event
from app.sync_jobs import calendar_pair_matches_job


class FakeHttpError(Exception):
	def __init__(self, status):
		self.resp = type("Response", (), {"status": status, "reason": "not found"})()


class FakeCalendarService:
	def __init__(self, calendars, calendar_metadata=None, fail_insert_calendar=None):
		self.calendar_events = calendars
		self.calendar_metadata = calendar_metadata or {calendar_id: {"id": calendar_id, "summary": calendar_id, "timeZone": "America/Los_Angeles"} for calendar_id in calendars}
		self.fail_insert_calendar = fail_insert_calendar
		self.update_count = 0
		self.insert_count = 0
		self.delete_count = 0
		self.event_get_count = 0
		self.event_list_count = 0
		self.mirror_search_count = 0

	def events(self):
		return self

	def calendars(self):
		return self

	def calendarList(self):
		return self

	def list(self, **kwargs):
		if "calendarId" not in kwargs:
			return FakeRequest(lambda: {"items": list(self.calendar_metadata.values())})
		calendar_id = kwargs["calendarId"]
		self.event_list_count += 1
		if "privateExtendedProperty" in kwargs:
			self.mirror_search_count += 1
		items = list(self.calendar_events[calendar_id].values())
		return FakeRequest(lambda: {"items": items, "nextSyncToken": f"sync-token-{calendar_id}"})

	def get(self, calendarId, eventId=None):
		def execute():
			if eventId is None:
				return self.calendar_metadata.get(calendarId, {"id": calendarId, "summary": calendarId, "timeZone": "America/Los_Angeles"})
			self.event_get_count += 1
			if eventId not in self.calendar_events[calendarId]:
				from app import sync
				raise sync.HttpError(FakeHttpError(404).resp, b"not found")
			return self.calendar_events[calendarId][eventId]
		return FakeRequest(execute)

	def update(self, calendarId, eventId, body, **kwargs):
		def execute():
			self.update_count += 1
			current = self.calendar_events[calendarId][eventId]
			updated = {**current, **body, "id": eventId, "etag": f"{current.get('etag', 'etag')}-u{self.update_count}", "created": current.get("created"), "status": current.get("status", "confirmed"), "_conferenceDataVersion": kwargs.get("conferenceDataVersion"), "_sendUpdates": kwargs.get("sendUpdates")}
			if "conferenceData" not in body:
				updated.pop("conferenceData", None)
			self.calendar_events[calendarId][eventId] = updated
			return updated
		return FakeRequest(execute)

	def insert(self, calendarId, body, **kwargs):
		def execute():
			self.insert_count += 1
			if calendarId == self.fail_insert_calendar:
				raise RuntimeError("insert failed")
			event_id = f"created-{self.insert_count}"
			created = {**body, "id": event_id, "etag": f"inserted-{self.insert_count}", "created": "2026-05-17T12:00:00Z", "status": "confirmed", "_conferenceDataVersion": kwargs.get("conferenceDataVersion"), "_sendUpdates": kwargs.get("sendUpdates")}
			self.calendar_events[calendarId][event_id] = created
			return created
		return FakeRequest(execute)

	def delete(self, calendarId, eventId, **kwargs):
		def execute():
			self.delete_count += 1
			if eventId not in self.calendar_events[calendarId]:
				from app import sync
				raise sync.HttpError(FakeHttpError(404).resp, b"not found")
			self.calendar_events[calendarId][eventId] = {**self.calendar_events[calendarId][eventId], "status": "cancelled", "_sendUpdates": kwargs.get("sendUpdates")}
			return {}
		return FakeRequest(execute)


class FakeRequest:
	def __init__(self, execute):
		self.execute = execute


def make_pair(pair_id=1):
	return CalendarPair(id=pair_id, user_id=1, timed_calendar_id="timed-cal", allday_calendar_id="daily-cal", backup_calendar_id="backup-cal", timed_sync_token=None, allday_sync_token=None)


def make_meet(code="abc-defg-hij"):
	return {"entryPoints": [{"entryPointType": "video", "uri": f"https://meet.google.com/{code}"}], "conferenceSolution": {"name": "Google Meet"}}


def make_timed_event(event_id="timed1", etag="t1", summary="Appointment", hour=9, duration=60, created="2026-05-17T09:00:00Z", location="Office", conference_data=None, status="confirmed"):
	start = datetime(2026, 5, 17, hour, 0)
	end = start + timedelta(minutes=duration)
	event = {"id": event_id, "etag": etag, "created": created, "summary": summary, "description": "Notes", "location": location, "start": {"dateTime": start.isoformat(), "timeZone": "America/Los_Angeles"}, "end": {"dateTime": end.isoformat(), "timeZone": "America/Los_Angeles"}, "status": status}
	if conference_data is not None:
		event["conferenceData"] = conference_data
	return event


def make_allday_event(event_id="daily1", etag="a1", summary="9am Appointment", created="2026-05-17T09:01:00Z", location="Office", conference_data=None, status="confirmed"):
	event = {"id": event_id, "etag": etag, "created": created, "summary": summary, "description": "Notes", "location": location, "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}, "status": status}
	if conference_data is not None:
		event["conferenceData"] = conference_data
	return event


def make_mapping(pair_id=1, status=TIMED_TO_ALLDAY):
	return EventMapping(calendar_pair_id=pair_id, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=status)


def make_mapped_service(timed=None, allday=None):
	timed = timed or make_timed_event()
	allday = allday or make_allday_event()
	return FakeCalendarService({"timed-cal": {timed["id"]: timed}, "daily-cal": {allday["id"]: allday}, "backup-cal": {}})


class SyncJobPairTests(unittest.TestCase):
	def test_calendar_pair_matches_job_when_ids_are_unchanged(self):
		pair = CalendarPair(user_id=7, timed_calendar_id="test-a", allday_calendar_id="test-b", backup_calendar_id="test-c")
		job = SyncJob(user_id=7, source_calendar_id="test-a", target_calendar_id="test-b", backup_calendar_id="test-c", friendly_name="Test job")
		self.assertTrue(calendar_pair_matches_job(pair, job))

	def test_calendar_pair_does_not_match_job_after_setup_mutates_pair(self):
		pair = CalendarPair(user_id=7, timed_calendar_id="real-c", allday_calendar_id="real-d", backup_calendar_id="real-e")
		job = SyncJob(user_id=7, source_calendar_id="test-a", target_calendar_id="test-b", backup_calendar_id="test-c", friendly_name="Test job")
		self.assertFalse(calendar_pair_matches_job(pair, job))

	def test_calendar_pair_does_not_match_job_when_backup_differs(self):
		pair = CalendarPair(user_id=7, timed_calendar_id="test-a", allday_calendar_id="test-b", backup_calendar_id="real-c")
		job = SyncJob(user_id=7, source_calendar_id="test-a", target_calendar_id="test-b", backup_calendar_id="test-c", friendly_name="Test job")
		self.assertFalse(calendar_pair_matches_job(pair, job))


class GoogleCredentialTests(unittest.TestCase):
	def test_invalid_grant_refresh_error_requires_google_reconnect(self):
		token = OAuthToken(user_id=1, access_token="old-access-token", refresh_token="old-refresh-token",
			token_uri="https://oauth2.googleapis.com/token", client_id="client-id", client_secret="client-secret",
			scopes="https://www.googleapis.com/auth/calendar", expiry=datetime.now(timezone.utc) - timedelta(minutes=5))
		with patch("app.google_client.Credentials.refresh", side_effect=RefreshError("invalid_grant: Token has been expired or revoked.")):
			with self.assertRaisesRegex(GoogleReconnectRequiredError, GOOGLE_RECONNECT_MESSAGE):
				credentials_from_token(token)


def make_named_service(timed_events=None, allday_events=None):
	timed_events = timed_events or {}
	allday_events = allday_events or {}
	return FakeCalendarService(
		{"timed-cal": timed_events, "daily-cal": allday_events, "backup-cal": {}},
		{"timed-cal": {"id": "timed-cal", "summary": "Hourly Work", "timeZone": "America/Los_Angeles"}, "daily-cal": {"id": "daily-cal", "summary": "Daily Plan", "timeZone": "America/Los_Angeles"}, "backup-cal": {"id": "backup-cal", "summary": "Backup", "timeZone": "America/Los_Angeles"}},
	)


class SyncHelperTests(unittest.TestCase):
	def test_timed_event_to_allday_event(self):
		conference_data = {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}], "conferenceSolution": {"name": "Google Meet"}}
		event = {"id": "abc123", "summary": "Doctor", "description": "Bring forms", "location": "Clinic", "conferenceData": conference_data, "attendees": [{"email": "organizer@example.com"}], "start": {"dateTime": "2026-05-17T14:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T15:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		result = timed_event_to_allday_event(event, "timed@example.com")
		self.assertEqual(result["summary"], "2pm Doctor")
		self.assertEqual(result["start"], {"date": "2026-05-17"})
		self.assertEqual(result["end"], {"date": "2026-05-18"})
		self.assertEqual(result["location"], "Clinic")
		self.assertEqual(result["description"], "Bring forms")
		self.assertEqual(result["conferenceData"], conference_data)
		self.assertNotIn("attendees", result)
		self.assertEqual(result["extendedProperties"]["private"]["sourceEventId"], "abc123")
		self.assertEqual(result["extendedProperties"]["private"]["syncDirection"], TIMED_TO_ALLDAY)

	def test_timed_event_to_allday_event_uses_hourly_calendar_timezone(self):
		event = {"id": "abc123", "summary": "Remote call", "description": "Bring forms", "start": {"dateTime": "2026-05-17T14:00:00-04:00", "timeZone": "America/New_York"}, "end": {"dateTime": "2026-05-17T15:00:00-04:00", "timeZone": "America/New_York"}}
		result = timed_event_to_allday_event(event, "timed@example.com", "America/Los_Angeles")
		self.assertEqual(result["summary"], "11am Remote call")
		self.assertEqual(result["start"], {"date": "2026-05-17"})
		self.assertEqual(result["end"], {"date": "2026-05-18"})

	def test_sync_timed_event_creates_daily_title_in_hourly_calendar_timezone(self):
		db_session.rollback()
		pair_id = 987662
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		event = {"id": "timed-ny", "etag": "t1", "created": "2026-05-17T09:00:00Z", "summary": "Remote call", "start": {"dateTime": "2026-06-17T14:00:00-04:00", "timeZone": "America/New_York"}, "end": {"dateTime": "2026-06-17T15:00:00-04:00", "timeZone": "America/New_York"}}
		service = FakeCalendarService({"timed-cal": {"timed-ny": event}, "daily-cal": {}})
		try:
			self.assertEqual(sync_timed_event(service, make_pair(pair_id), event, "America/Los_Angeles"), "created")
			self.assertEqual(service.calendar_events["daily-cal"]["created-1"]["summary"], "11am Remote call")
			self.assertEqual(service.calendar_events["daily-cal"]["created-1"]["start"], {"date": "2026-06-17"})
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_sync_allday_event_creates_recurring_hourly_event_for_multiday_time_range(self):
		db_session.rollback()
		pair_id = 987663
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		event = {"id": "daily-range", "etag": "a1", "created": "2026-05-17T09:00:00Z", "summary": "Training 9am-3pm", "start": {"date": "2026-06-17"}, "end": {"date": "2026-06-20"}}
		service = FakeCalendarService({"timed-cal": {}, "daily-cal": {"daily-range": event}})
		try:
			self.assertEqual(sync_allday_event(service, make_pair(pair_id), event, "America/Los_Angeles"), "created")
			created = service.calendar_events["timed-cal"]["created-1"]
			self.assertEqual(created["summary"], "Training")
			self.assertEqual(created["start"], {"dateTime": "2026-06-17T09:00:00", "timeZone": "America/Los_Angeles"})
			self.assertEqual(created["end"], {"dateTime": "2026-06-17T15:00:00", "timeZone": "America/Los_Angeles"})
			self.assertEqual(created["recurrence"], ["RRULE:FREQ=DAILY;COUNT=3"])
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_allday_title_parser_finds_single_times_and_ranges(self):
		self.assertEqual(parse_allday_title_for_timed_event("2:30pm Doctor"), {"hour": 14, "minute": 30, "summary": "Doctor", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("14:00 Doctor"), {"hour": 14, "minute": 0, "summary": "Doctor", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("Dinner 5-7pm"), {"hour": 17, "minute": 0, "summary": "Dinner", "duration_minutes": 120})
		self.assertEqual(parse_allday_title_for_timed_event("5pm to 7pm Dinner"), {"hour": 17, "minute": 0, "summary": "Dinner", "duration_minutes": 120})
		self.assertEqual(parse_allday_title_for_timed_event("Focus btw 10 and 2pm"), {"hour": 10, "minute": 0, "summary": "Focus", "duration_minutes": 240})
		self.assertIsNone(parse_allday_title_for_timed_event("Doctor at 2"))

	def test_allday_title_parser_removes_particle_before_time(self):
		self.assertEqual(parse_allday_title_for_timed_event("Dinner at 7pm"), {"hour": 19, "minute": 0, "summary": "Dinner", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("Dinner from 7-8pm"), {"hour": 19, "minute": 0, "summary": "Dinner", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("Call around 7pm with Alex"), {"hour": 19, "minute": 0, "summary": "Call with Alex", "duration_minutes": 60})
		self.assertEqual(parse_allday_title_for_timed_event("Work from home 7pm"), {"hour": 19, "minute": 0, "summary": "Work from home", "duration_minutes": 60})

	def test_allday_event_to_timed_event_uses_time_when_clear(self):
		conference_data = {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/xyz-abcd-efg"}]}
		event = {"id": "daily1", "summary": "Dinner 5-7pm", "description": "Bring salad", "conferenceData": conference_data, "attendees": [{"email": "friend@example.com"}], "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles")
		self.assertEqual(result["summary"], "Dinner")
		self.assertEqual(result["start"], {"dateTime": "2026-05-17T17:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(result["end"], {"dateTime": "2026-05-17T19:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(result["description"], "Bring salad")
		self.assertEqual(result["conferenceData"], conference_data)
		self.assertNotIn("attendees", result)
		self.assertEqual(result["extendedProperties"]["private"]["syncDirection"], ALLDAY_TO_TIMED)

	def test_multiday_allday_event_to_timed_event_repeats_each_day_when_time_is_clear(self):
		event = {"id": "daily1", "summary": "Training 9am-3pm", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-20"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles")
		self.assertEqual(result["summary"], "Training")
		self.assertEqual(result["start"], {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(result["end"], {"dateTime": "2026-05-17T15:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(result["recurrence"], ["RRULE:FREQ=DAILY;COUNT=3"])

	def test_allday_event_to_timed_event_removes_particle_before_time(self):
		event = {"id": "daily1", "summary": "Dinner at 7pm", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles")
		self.assertEqual(result["summary"], "Dinner")
		self.assertEqual(result["start"], {"dateTime": "2026-05-17T19:00:00", "timeZone": "America/Los_Angeles"})

	def test_allday_rename_without_time_keeps_existing_hourly_time(self):
		event = {"id": "daily1", "summary": "Appointment2", "description": "New notes", "location": "Clinic", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		existing = {"start": {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00", "timeZone": "America/Los_Angeles"}}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles", existing)
		self.assertEqual(result["summary"], "Appointment2")
		self.assertEqual(result["start"], existing["start"])
		self.assertEqual(result["end"], existing["end"])
		self.assertEqual(result["description"], "New notes")
		self.assertEqual(result["location"], "Clinic")

	def test_allday_rename_without_time_keeps_existing_hourly_recurrence(self):
		event = {"id": "daily1", "summary": "Appointment2", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-20"}}
		existing = {"start": {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00", "timeZone": "America/Los_Angeles"}, "recurrence": ["RRULE:FREQ=DAILY;COUNT=3"]}
		result = allday_event_to_timed_calendar_event(event, "allday@example.com", "America/Los_Angeles", existing)
		self.assertEqual(result["start"], existing["start"])
		self.assertEqual(result["end"], existing["end"])
		self.assertEqual(result["recurrence"], existing["recurrence"])

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

	def test_unmapped_cancelled_events_are_ignored_deleted(self):
		before_cutoff = query_start_for_overlapping_events("America/Los_Angeles").date() - timedelta(days=1)
		pair = make_pair(987655)
		timed_event = {"id": "old-cancelled-timed", "status": "cancelled", "start": {"dateTime": f"{before_cutoff.isoformat()}T09:00:00-07:00"}}
		allday_event = {"id": "old-cancelled-daily", "status": "cancelled", "start": {"date": before_cutoff.isoformat()}}
		self.assertEqual(sync_timed_event(FakeCalendarService({"timed-cal": {}, "daily-cal": {}}), pair, timed_event, "America/Los_Angeles"), "ignored_deleted")
		self.assertEqual(sync_allday_event(FakeCalendarService({"timed-cal": {}, "daily-cal": {}}), pair, allday_event, "America/Los_Angeles"), "ignored_deleted")

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

	def test_calendar_setup_requires_backup_and_distinct_calendars(self):
		missing_backup = validate_calendar_setup("hourly", "daily", None)
		same_calendar = validate_calendar_setup("hourly", "daily", "daily")
		assert missing_backup is not None
		assert same_calendar is not None
		self.assertEqual(missing_backup["error"], "missing_calendar_id")
		self.assertEqual(same_calendar["error"], "same_calendar")
		self.assertIsNone(validate_calendar_setup("hourly", "daily", "backup"))

	def test_serialize_datetime_marks_naive_values_as_utc(self):
		self.assertEqual(serialize_datetime(datetime(2026, 5, 17, 20, 30)), "2026-05-17T20:30:00Z")

	def test_mapped_daily_rename_updates_hourly_event(self):
		timed = {"id": "timed1", "etag": "t1", "created": "2026-05-17T09:00:00Z", "summary": "Appointment", "start": {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00", "timeZone": "America/Los_Angeles"}}
		conference_data = {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/xyz-abcd-efg"}]}
		allday = {"id": "daily1", "etag": "a2", "created": "2026-05-17T09:01:00Z", "summary": "9am Appointment2", "description": "Updated", "location": "Clinic", "conferenceData": conference_data, "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}})
		pair = make_pair()
		mapping = EventMapping(calendar_pair_id=1, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=TIMED_TO_ALLDAY)
		self.assertEqual(sync_mapped_pair_from_allday(service, pair, mapping, allday, "America/Los_Angeles"), "updated")
		updated = service.calendar_events["timed-cal"]["timed1"]
		self.assertEqual(updated["summary"], "Appointment2")
		self.assertEqual(updated["description"], "Updated")
		self.assertEqual(updated["location"], "Clinic")
		self.assertEqual(updated["conferenceData"], conference_data)
		self.assertEqual(updated["_conferenceDataVersion"], 1)
		self.assertEqual(updated["_sendUpdates"], "none")
		self.assertEqual(updated["start"], {"dateTime": "2026-05-17T09:00:00", "timeZone": "America/Los_Angeles"})

	def test_mapped_hourly_rename_updates_daily_event(self):
		timed = {"id": "timed1", "etag": "t2", "created": "2026-05-17T09:00:00Z", "summary": "Appointment3", "description": "", "location": "", "start": {"dateTime": "2026-05-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		allday = {"id": "daily1", "etag": "a1", "created": "2026-05-17T09:01:00Z", "summary": "9am Appointment", "description": "Old", "location": "Office", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}})
		pair = make_pair()
		mapping = EventMapping(calendar_pair_id=1, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=TIMED_TO_ALLDAY)
		self.assertEqual(sync_mapped_pair_from_timed(service, pair, mapping, timed, "America/Los_Angeles"), "updated")
		updated = service.calendar_events["daily-cal"]["daily1"]
		self.assertEqual(updated["summary"], "9am Appointment3")
		self.assertEqual(updated["description"], "")
		self.assertEqual(updated["location"], "")
		self.assertEqual(updated["_sendUpdates"], "none")

	def test_both_sides_changed_earlier_created_event_wins_and_records_conflict(self):
		db_session.rollback()
		timed = {"id": "timed1", "etag": "t2", "created": "2026-05-17T09:00:00Z", "summary": "Original wins", "start": {"dateTime": "2026-05-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": "2026-05-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}}
		allday = {"id": "daily1", "etag": "a2", "created": "2026-05-17T09:01:00Z", "summary": "9am Later edit", "start": {"date": "2026-05-17"}, "end": {"date": "2026-05-18"}}
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}})
		pair = make_pair()
		mapping = EventMapping(calendar_pair_id=1, timed_event_id="timed1", allday_event_id="daily1", timed_etag="t1", allday_etag="a1", status=TIMED_TO_ALLDAY)
		try:
			self.assertEqual(sync_mapped_pair_from_allday(service, pair, mapping, allday, "America/Los_Angeles"), "updated")
			self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["summary"], "9am Original wins")
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()

	def test_mapped_hourly_time_change_updates_daily_title_and_date(self):
		timed = make_timed_event(etag="t2", hour=10)
		allday = make_allday_event()
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
		updated = service.calendar_events["daily-cal"]["daily1"]
		self.assertEqual(updated["summary"], "10am Appointment")
		self.assertEqual(updated["start"], {"date": "2026-05-17"})
		self.assertEqual(updated["end"], {"date": "2026-05-18"})
		self.assertEqual(mapping.timed_etag, "t2")

	def test_mapped_daily_title_time_change_updates_hourly_time(self):
		timed = make_timed_event()
		allday = make_allday_event(etag="a2", summary="10am Appointment")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_allday(service, make_pair(), mapping, allday, "America/Los_Angeles"), "updated")
		updated = service.calendar_events["timed-cal"]["timed1"]
		self.assertEqual(updated["summary"], "Appointment")
		self.assertEqual(updated["start"], {"dateTime": "2026-05-17T10:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(updated["end"], {"dateTime": "2026-05-17T11:00:00", "timeZone": "America/Los_Angeles"})
		self.assertEqual(mapping.allday_etag, "a2")

	def test_both_time_changes_hourly_original_wins_and_records_conflict(self):
		db_session.rollback()
		timed = make_timed_event(etag="t2", hour=10, created="2026-05-17T09:00:00Z")
		allday = make_allday_event(etag="a2", summary="11am Appointment", created="2026-05-17T09:01:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		try:
			self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
			self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["summary"], "10am Appointment")
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()

	def test_both_time_changes_daily_original_wins_and_records_conflict(self):
		db_session.rollback()
		timed = make_timed_event(etag="t2", hour=10, created="2026-05-17T09:02:00Z")
		allday = make_allday_event(etag="a2", summary="11am Appointment", created="2026-05-17T09:00:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping(status=ALLDAY_TO_TIMED)
		try:
			self.assertEqual(sync_mapped_pair_from_allday(service, make_pair(), mapping, allday, "America/Los_Angeles"), "updated")
			updated = service.calendar_events["timed-cal"]["timed1"]
			self.assertEqual(updated["start"], {"dateTime": "2026-05-17T11:00:00", "timeZone": "America/Los_Angeles"})
			self.assertEqual(updated["end"], {"dateTime": "2026-05-17T12:00:00", "timeZone": "America/Los_Angeles"})
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()

	def test_compatible_hourly_time_and_daily_title_time_edits_merge_without_conflict(self):
		db_session.rollback()
		timed = make_timed_event(etag="t2", hour=10)
		allday = make_allday_event(etag="a2", summary="10am Appointment")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		try:
			self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
			self.assertEqual(service.update_count, 0)
			self.assertEqual(mapping.timed_etag, "t2")
			self.assertEqual(mapping.allday_etag, "a2")
			self.assertFalse(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()

	def test_hourly_time_and_conflicting_daily_title_edit_original_wins(self):
		db_session.rollback()
		timed = make_timed_event(etag="t2", hour=10, summary="Appointment")
		allday = make_allday_event(etag="a2", summary="10am Dentist")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		try:
			self.assertEqual(sync_mapped_pair_from_allday(service, make_pair(), mapping, allday, "America/Los_Angeles"), "updated")
			self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["summary"], "10am Appointment")
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()

	def test_mapped_hourly_location_change_updates_daily_location(self):
		timed = make_timed_event(etag="t2", location="Room A")
		allday = make_allday_event(location="Office")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
		self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["location"], "Room A")

	def test_mapped_daily_location_change_updates_hourly_location(self):
		timed = make_timed_event(location="Office")
		allday = make_allday_event(etag="a2", location="Room B")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_allday(service, make_pair(), mapping, allday, "America/Los_Angeles"), "updated")
		self.assertEqual(service.calendar_events["timed-cal"]["timed1"]["location"], "Room B")

	def test_both_location_changes_original_wins_and_records_conflict(self):
		db_session.rollback()
		timed = make_timed_event(etag="t2", location="Room A", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(etag="a2", location="Room B", created="2026-05-17T09:01:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		try:
			self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
			self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["location"], "Room A")
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()

	def test_mapped_hourly_meet_change_updates_daily_meet(self):
		meet_b = make_meet("bbb-bbbb-bbb")
		timed = make_timed_event(etag="t2", conference_data=meet_b)
		allday = make_allday_event(conference_data=make_meet("aaa-aaaa-aaa"))
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
		updated = service.calendar_events["daily-cal"]["daily1"]
		self.assertEqual(updated["conferenceData"], meet_b)
		self.assertEqual(updated["_conferenceDataVersion"], 1)

	def test_mapped_daily_meet_change_updates_hourly_meet(self):
		meet_c = make_meet("ccc-cccc-ccc")
		timed = make_timed_event(conference_data=make_meet("aaa-aaaa-aaa"))
		allday = make_allday_event(etag="a2", conference_data=meet_c)
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_allday(service, make_pair(), mapping, allday, "America/Los_Angeles"), "updated")
		updated = service.calendar_events["timed-cal"]["timed1"]
		self.assertEqual(updated["conferenceData"], meet_c)
		self.assertEqual(updated["_conferenceDataVersion"], 1)

	def test_mapped_hourly_meet_removal_removes_daily_meet(self):
		timed = make_timed_event(etag="t2")
		allday = make_allday_event(conference_data=make_meet())
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_timed(service, make_pair(), mapping, timed, "America/Los_Angeles"), "updated")
		self.assertNotIn("conferenceData", service.calendar_events["daily-cal"]["daily1"])

	def test_mapped_daily_meet_removal_removes_hourly_meet(self):
		timed = make_timed_event(conference_data=make_meet())
		allday = make_allday_event(etag="a2")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping()
		self.assertEqual(sync_mapped_pair_from_allday(service, make_pair(), mapping, allday, "America/Los_Angeles"), "updated")
		self.assertNotIn("conferenceData", service.calendar_events["timed-cal"]["timed1"])

	def test_deleted_hourly_event_propagates_to_unchanged_daily_event_with_backup(self):
		db_session.rollback()
		pair_id = 987657
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(status="cancelled", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(created="2026-05-17T09:01:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			self.assertEqual(sync_timed_event(service, make_pair(pair_id), timed, "America/Los_Angeles"), "propagated_deleted")
			self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["status"], "cancelled")
			backup = service.calendar_events["backup-cal"]["created-1"]
			self.assertEqual(backup["summary"], "9am Appointment")
			self.assertEqual(backup["extendedProperties"]["private"]["calendarSyncBackup"], "true")
			self.assertEqual(backup["extendedProperties"]["private"]["originalCalendarId"], "daily-cal")
			self.assertEqual(backup["extendedProperties"]["private"]["deletedEventId"], "timed1")
			db_session.commit()
			self.assertIsNone(EventMapping.query.filter_by(calendar_pair_id=pair_id).one_or_none())
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_delete_propagation_stops_when_live_daily_event_changed(self):
		db_session.rollback()
		pair_id = 987658
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(status="cancelled", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(etag="a2", summary="9am Edited mirror", created="2026-05-17T09:01:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			self.assertEqual(sync_timed_event(service, make_pair(pair_id), timed, "America/Los_Angeles"), "conflict")
			self.assertEqual(service.delete_count, 0)
			self.assertEqual(service.insert_count, 0)
			self.assertIsNotNone(EventMapping.query.filter_by(calendar_pair_id=pair_id).one_or_none())
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_deleted_daily_event_propagates_to_unchanged_hourly_event_with_backup(self):
		db_session.rollback()
		pair_id = 987668
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(created="2026-05-17T09:00:00Z")
		allday = make_allday_event(status="cancelled", created="2026-05-17T09:01:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			self.assertEqual(sync_allday_event(service, make_pair(pair_id), allday, "America/Los_Angeles"), "propagated_deleted")
			self.assertEqual(service.calendar_events["timed-cal"]["timed1"]["status"], "cancelled")
			backup = service.calendar_events["backup-cal"]["created-1"]
			self.assertEqual(backup["summary"], "Appointment")
			self.assertEqual(backup["start"], timed["start"])
			self.assertEqual(backup["extendedProperties"]["private"]["originalCalendarId"], "timed-cal")
			self.assertEqual(backup["extendedProperties"]["private"]["deletedEventId"], "daily1")
			db_session.commit()
			self.assertIsNone(EventMapping.query.filter_by(calendar_pair_id=pair_id).one_or_none())
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_delete_propagation_stops_when_live_hourly_event_changed(self):
		db_session.rollback()
		pair_id = 987659
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(etag="t2", summary="Edited Appointment", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(status="cancelled", created="2026-05-17T09:01:00Z")
		service = make_mapped_service(timed, allday)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			self.assertEqual(sync_allday_event(service, make_pair(pair_id), allday, "America/Los_Angeles"), "conflict")
			self.assertEqual(service.delete_count, 0)
			self.assertEqual(service.insert_count, 0)
			self.assertIsNotNone(EventMapping.query.filter_by(calendar_pair_id=pair_id).one_or_none())
			self.assertTrue(any(isinstance(item, Conflict) for item in db_session.new))
		finally:
			db_session.rollback()
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_backup_insert_failure_stops_before_deleting_live_event(self):
		db_session.rollback()
		pair_id = 987669
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(status="cancelled", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(created="2026-05-17T09:01:00Z")
		service = FakeCalendarService({"timed-cal": {"timed1": timed}, "daily-cal": {"daily1": allday}, "backup-cal": {}}, fail_insert_calendar="backup-cal")
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			with self.assertRaises(RuntimeError):
				sync_timed_event(service, make_pair(pair_id), timed, "America/Los_Angeles")
			self.assertEqual(service.delete_count, 0)
			self.assertEqual(service.calendar_events["daily-cal"]["daily1"]["status"], "confirmed")
			self.assertIsNotNone(EventMapping.query.filter_by(calendar_pair_id=pair_id).one_or_none())
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_clear_deleted_event_mappings_removes_only_pairs_deleted_on_both_sides(self):
		db_session.rollback()
		pair_id = 987654
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		service = FakeCalendarService({
			"timed-cal": {
				"timed-cancelled": {"id": "timed-cancelled", "status": "cancelled"},
				"timed-live": {"id": "timed-live", "status": "confirmed"},
				"timed-single-deleted": {"id": "timed-single-deleted", "status": "cancelled"},
			},
			"daily-cal": {
				"daily-cancelled": {"id": "daily-cancelled", "status": "cancelled"},
				"daily-live": {"id": "daily-live", "status": "confirmed"},
				"daily-single-live": {"id": "daily-single-live", "status": "confirmed"},
			},
		})
		pair = make_pair(pair_id)
		removed = EventMapping(calendar_pair_id=pair_id, timed_event_id="timed-cancelled", allday_event_id="daily-cancelled")
		missing_removed = EventMapping(calendar_pair_id=pair_id, timed_event_id="missing-timed", allday_event_id="missing-daily")
		kept_live = EventMapping(calendar_pair_id=pair_id, timed_event_id="timed-live", allday_event_id="daily-live")
		kept_partial = EventMapping(calendar_pair_id=pair_id, timed_event_id="timed-single-deleted", allday_event_id="daily-single-live")
		db_session.add_all([removed, missing_removed, kept_live, kept_partial])
		db_session.commit()
		try:
			result = clear_deleted_event_mappings(service, pair)
			self.assertEqual(result, {"checked": 4, "cleared": 2, "kept": 2})
			remaining = {mapping.timed_event_id for mapping in EventMapping.query.filter_by(calendar_pair_id=pair_id).all()}
			self.assertEqual(remaining, {"timed-live", "timed-single-deleted"})
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_run_message_omits_ignored_deleted_events(self):
		db_session.rollback()
		before_cutoff = query_start_for_overlapping_events("America/Los_Angeles").date() - timedelta(days=1)
		service = FakeCalendarService({
			"timed-cal": {"old-cancelled-timed": {"id": "old-cancelled-timed", "status": "cancelled", "start": {"dateTime": f"{before_cutoff.isoformat()}T09:00:00-07:00"}}},
			"daily-cal": {"old-cancelled-daily": {"id": "old-cancelled-daily", "status": "cancelled", "start": {"date": before_cutoff.isoformat()}}},
		})
		pair = make_pair(987656)
		try:
			run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			self.assertEqual(run.message, "")
			self.assertEqual(pair.timed_sync_token, "sync-token-timed-cal")
			self.assertEqual(pair.allday_sync_token, "sync-token-daily-cal")
		finally:
			db_session.rollback()

	def test_run_message_omits_mapped_pair_already_deleted_on_both_sides(self):
		db_session.rollback()
		pair_id = 987663
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(status="cancelled", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(status="cancelled", created="2026-05-17T09:01:00Z")
		service = make_named_service({"timed1": timed}, {"daily1": allday})
		pair = make_pair(pair_id)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			with self.assertLogs("app.sync", level="INFO") as logs:
				run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			self.assertEqual(run.message, "")
			self.assertEqual(service.delete_count, 0)
			self.assertIsNone(EventMapping.query.filter_by(calendar_pair_id=pair_id).one_or_none())
			log_text = "\n".join(logs.output)
			self.assertNotIn("Sync deleted", log_text)
			self.assertIn(f"Sync summary for pair {pair_id}: status=success processed=1 hourly, 1 daily; created=0, updated=0, deleted=0, propagated_deleted=0", log_text)
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_run_logs_created_event_and_summary_with_calendar_names(self):
		db_session.rollback()
		pair_id = 987660
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(event_id="timed-new", summary="New Appointment")
		timed["start"] = {"dateTime": "2026-06-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"}
		timed["end"] = {"dateTime": "2026-06-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}
		service = make_named_service({"timed-new": timed}, {})
		pair = make_pair(pair_id)
		try:
			with self.assertLogs("app.sync", level="INFO") as logs:
				run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			log_text = "\n".join(logs.output)
			self.assertIn("Sync created daily calendar event on Daily Plan: id=created-1 title='9am New Appointment'", log_text)
			self.assertIn(f"Sync summary for pair {pair_id}: status=success processed=1 hourly, 0 daily; created=1, updated=0, deleted=0", log_text)
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_run_logs_updated_event_with_target_calendar_name(self):
		db_session.rollback()
		pair_id = 987661
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(etag="t2", hour=10)
		timed["start"] = {"dateTime": "2026-06-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}
		timed["end"] = {"dateTime": "2026-06-17T11:00:00-07:00", "timeZone": "America/Los_Angeles"}
		allday = make_allday_event()
		allday["start"] = {"date": "2026-06-17"}
		allday["end"] = {"date": "2026-06-18"}
		service = make_named_service({"timed1": timed}, {"daily1": allday})
		pair = make_pair(pair_id)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			with self.assertLogs("app.sync", level="INFO") as logs:
				run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			log_text = "\n".join(logs.output)
			self.assertIn("Sync updated daily calendar event on Daily Plan: id=daily1 title='10am Appointment'", log_text)
			self.assertIn(f"Sync summary for pair {pair_id}: status=success processed=1 hourly, 1 daily; created=0, updated=1", log_text)
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_run_logs_propagated_deleted_event_with_target_calendar_name(self):
		db_session.rollback()
		pair_id = 987662
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event(status="cancelled", created="2026-05-17T09:00:00Z")
		allday = make_allday_event(created="2026-05-17T09:01:00Z")
		service = make_named_service({"timed1": timed}, {"daily1": allday})
		pair = make_pair(pair_id)
		mapping = make_mapping(pair_id=pair_id)
		db_session.add(mapping)
		db_session.commit()
		try:
			with self.assertLogs("app.sync", level="INFO") as logs:
				run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			log_text = "\n".join(logs.output)
			self.assertIn("Sync backed up backup calendar event on Backup: id=created-1 title='9am Appointment'", log_text)
			self.assertIn("Sync propagated_deleted daily calendar event on Daily Plan: id=daily1 title='9am Appointment'", log_text)
			self.assertIn(f"Sync summary for pair {pair_id}: status=success processed=1 hourly, 1 daily; created=0, updated=0, deleted=0, propagated_deleted=1", log_text)
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_run_sync_requires_backup_calendar(self):
		pair = CalendarPair(id=987670, user_id=1, timed_calendar_id="timed-cal", allday_calendar_id="daily-cal", backup_calendar_id=None, timed_sync_token=None, allday_sync_token=None)
		service = FakeCalendarService({"timed-cal": {}, "daily-cal": {}})
		with self.assertRaises(SyncSetupRequiredError):
			run_sync_for_pair(service, pair)

	def test_run_sync_skips_google_year_zero_dates(self):
		db_session.rollback()
		pair_id = 987671
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		yearless_birthday = {"id": "birthday-june-1", "etag": "b1", "created": "2026-06-01T09:00:00Z", "summary": "Birthday", "start": {"date": "0000-06-01"}, "end": {"date": "0000-06-02"}, "status": "confirmed"}
		service = make_named_service({"birthday-june-1": yearless_birthday}, {})
		pair = make_pair(pair_id)
		with self.assertLogs("app.sync", level="WARNING") as logs:
			run = run_sync_for_pair(service, pair)
		self.assertEqual(run.status, "success")
		self.assertEqual(run.message, "1 skipped")
		self.assertIn("Skipping Google event with unsupported year-zero date: id=birthday-june-1 title='Birthday'", "\n".join(logs.output))

	def test_run_sync_skips_mapped_pair_with_google_year_zero_counterpart(self):
		db_session.rollback()
		pair_id = 987673
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed = make_timed_event()
		timed["start"] = {"dateTime": "2026-06-17T09:00:00-07:00", "timeZone": "America/Los_Angeles"}
		timed["end"] = {"dateTime": "2026-06-17T10:00:00-07:00", "timeZone": "America/Los_Angeles"}
		yearless_allday = make_allday_event()
		yearless_allday["start"] = {"date": "0000-06-01"}
		yearless_allday["end"] = {"date": "0000-06-02"}
		service = make_named_service({"timed1": timed}, {"daily1": yearless_allday})
		pair = make_pair(pair_id)
		db_session.add(make_mapping(pair_id=pair_id))
		db_session.commit()
		try:
			with self.assertLogs("app.sync", level="WARNING") as logs:
				run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			self.assertEqual(run.message, "2 skipped")
			log_text = "\n".join(logs.output)
			self.assertIn("Skipping mapped pair with unsupported year-zero date: timed_id=timed1", log_text)
			self.assertIn("Skipping Google event with unsupported year-zero date: id=daily1 title='9am Appointment'", log_text)
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_run_sync_ignores_google_year_zero_created_timestamps(self):
		db_session.rollback()
		pair_id = 987675
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		event_date = datetime.now().date()
		timed = make_timed_event(etag="t2", summary="CANCELLED", created=f"{event_date.isoformat()}T18:45:00Z")
		timed["start"] = {"dateTime": f"{event_date.isoformat()}T18:45:00-07:00", "timeZone": "America/Los_Angeles"}
		timed["end"] = {"dateTime": f"{event_date.isoformat()}T19:45:00-07:00", "timeZone": "America/Los_Angeles"}
		allday = make_allday_event(etag="a2", summary="6:45pm Original title", created="0000-01-01T00:00:00Z")
		allday["start"] = {"date": event_date.isoformat()}
		allday["end"] = {"date": (event_date + timedelta(days=1)).isoformat()}
		service = make_named_service({"timed1": timed}, {"daily1": allday})
		pair = make_pair(pair_id)
		db_session.add(make_mapping(pair_id=pair_id))
		db_session.commit()
		try:
			with self.assertLogs("app.sync", level="WARNING") as logs:
				run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			self.assertIn("Ignoring unsupported year-zero Google datetime: field=created", "\n".join(logs.output))
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			Conflict.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()

	def test_parse_google_datetime_ignores_year_zero_with_context(self):
		event = {"id": "birthday", "summary": "Birthday", "created": "0000-01-01T00:00:00Z"}
		with self.assertLogs("app.sync", level="WARNING") as logs:
			self.assertIsNone(parse_google_datetime({"dateTime": "0000-01-01T00:00:00Z"}, event, "created"))
		self.assertIn("field=created", "\n".join(logs.output))
		self.assertIn("'id': 'birthday'", "\n".join(logs.output))

	def test_parse_google_datetime_reports_malformed_values_with_context(self):
		event = {"id": "bad-date", "summary": "Bad date"}
		with self.assertRaisesRegex(ValueError, "Invalid Google datetime for start: 'bad-date'.*'id': 'bad-date'"):
			parse_google_datetime({"dateTime": "bad-date"}, event, "start")

	def test_run_sync_logs_event_context_on_unexpected_error(self):
		pair_id = 987674
		bad_event = {"id": "bad-date", "etag": "b1", "created": "2026-06-01T09:00:00Z", "summary": "Bad date", "start": {"dateTime": "bad-date"}, "end": {"dateTime": "2026-06-01T10:00:00-07:00"}, "status": "confirmed"}
		service = make_named_service({"bad-date": bad_event}, {})
		pair = make_pair(pair_id)
		with self.assertLogs("app.sync", level="ERROR") as logs:
			with self.assertRaises(ValueError):
				run_sync_for_pair(service, pair)
		log_text = "\n".join(logs.output)
		self.assertIn(f"Sync failed for pair {pair_id} during process hourly", log_text)
		self.assertIn("'id': 'bad-date'", log_text)

	def test_sync_allday_event_skips_google_year_zero_dates(self):
		pair = make_pair(987672)
		event = {"id": "birthday-june-1", "etag": "b1", "summary": "Birthday", "start": {"date": "0000-06-01"}, "end": {"date": "0000-06-02"}, "status": "confirmed"}
		service = make_named_service({}, {"birthday-june-1": event})
		self.assertEqual(sync_allday_event(service, pair, event, "America/Los_Angeles"), "skipped")

	def test_full_sync_reuses_listed_events_for_existing_mappings(self):
		db_session.rollback()
		pair_id = 987664
		EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
		db_session.commit()
		timed_events = {}
		allday_events = {}
		mappings = []
		for index in range(3):
			timed_id = f"timed-{index}"
			allday_id = f"daily-{index}"
			hour = 9 + index
			timed_events[timed_id] = {"id": timed_id, "etag": f"t{index}", "created": "2026-06-17T09:00:00Z", "summary": f"Appointment {index}", "description": "Notes", "location": "Office", "start": {"dateTime": f"2026-06-17T{hour:02d}:00:00-07:00", "timeZone": "America/Los_Angeles"}, "end": {"dateTime": f"2026-06-17T{hour + 1:02d}:00:00-07:00", "timeZone": "America/Los_Angeles"}, "status": "confirmed"}
			allday_events[allday_id] = {"id": allday_id, "etag": f"a{index}", "created": "2026-06-17T09:01:00Z", "summary": f"{hour if hour <= 12 else hour - 12}am Appointment {index}", "description": "Notes", "location": "Office", "start": {"date": "2026-06-17"}, "end": {"date": "2026-06-18"}, "status": "confirmed", "extendedProperties": {"private": {"calendarSyncApp": "true", "syncDirection": TIMED_TO_ALLDAY, "sourceEventId": timed_id, "sourceCalendarId": "timed-cal"}}}
			mappings.append(EventMapping(calendar_pair_id=pair_id, timed_event_id=timed_id, allday_event_id=allday_id, timed_etag=f"t{index}", allday_etag=f"a{index}", status=TIMED_TO_ALLDAY))
		service = make_named_service(timed_events, allday_events)
		pair = make_pair(pair_id)
		db_session.add_all(mappings)
		db_session.commit()
		try:
			run = run_sync_for_pair(service, pair)
			self.assertEqual(run.status, "success")
			self.assertEqual(service.event_list_count, 2)
			self.assertEqual(service.event_get_count, 0)
			self.assertEqual(service.mirror_search_count, 0)
			self.assertEqual(service.update_count, 0)
			self.assertEqual(service.insert_count, 0)
		finally:
			EventMapping.query.filter_by(calendar_pair_id=pair_id).delete()
			db_session.commit()


if __name__ == "__main__":
	unittest.main()
