import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from app.db import db_session
from app.future_sync import parse_allday_title_for_timed_event
from app.models import CalendarPair, Conflict, EventMapping, SyncRun, utcnow


SYNC_PRIVATE_PROPS = {"calendarSyncApp": "true"}
TIMED_TO_ALLDAY = "timed_to_allday"
ALLDAY_TO_TIMED = "allday_to_timed"


def is_all_day_event(event):
	return "date" in event.get("start", {})


def parse_google_datetime(value):
	raw = value.get("dateTime") or value.get("date")
	if not raw:
		return None
	if raw.endswith("Z"):
		raw = raw[:-1] + "+00:00"
	return datetime.fromisoformat(raw)


def event_timezone(event):
	tz_name = event.get("start", {}).get("timeZone") or event.get("end", {}).get("timeZone")
	return ZoneInfo(tz_name) if tz_name else None


def format_start_time(dt):
	hour = dt.hour % 12 or 12
	minute = f":{dt.minute:02d}" if dt.minute else ""
	suffix = "am" if dt.hour < 12 else "pm"
	return f"{hour}{minute}{suffix}"


def sync_private_props(event):
	return event.get("extendedProperties", {}).get("private", {})


def is_sync_generated_from(event, source_calendar_id=None, direction=None):
	props = sync_private_props(event)
	if props.get("calendarSyncApp") != "true":
		return False
	if source_calendar_id and props.get("sourceCalendarId") != source_calendar_id:
		return False
	if direction and props.get("syncDirection") != direction:
		return False
	return True


def timed_event_to_allday_event(event, source_calendar_id=None):
	start = parse_google_datetime(event.get("start", {}))
	if start is None:
		raise ValueError("Timed event is missing a start dateTime")
	tz = event_timezone(event)
	if tz and start.tzinfo:
		start = start.astimezone(tz)
	date = start.date().isoformat()
	summary = event.get("summary") or "(No title)"
	description_parts = ["Synced from timed calendar.", f"Original event ID: {event.get('id')}"]
	if event.get("description"):
		description_parts.extend(["", event["description"]])
	body = {
		"summary": f"{format_start_time(start)} {summary}",
		"start": {"date": date},
		"end": {"date": (start.date() + timedelta(days=1)).isoformat()},
		"description": "\n".join(description_parts),
		"extendedProperties": {"private": {**SYNC_PRIVATE_PROPS, "syncDirection": TIMED_TO_ALLDAY, "sourceEventId": event.get("id") or "", "sourceCalendarId": source_calendar_id or ""}},
	}
	if event.get("location"):
		body["location"] = event["location"]
	return body


def allday_event_to_timed_calendar_event(event, source_calendar_id=None, timezone_name="UTC"):
	if not is_all_day_event(event):
		raise ValueError("All-day source event is missing a start date")
	start_date = parse_google_datetime(event.get("start", {})).date()
	end_date = parse_google_datetime(event.get("end", {})).date() if event.get("end") else start_date + timedelta(days=1)
	parsed = parse_allday_title_for_timed_event(event.get("summary") or "")
	summary = (parsed or {}).get("summary") or event.get("summary") or "(No title)"
	description_parts = ["Synced from all-day calendar.", f"Original event ID: {event.get('id')}"]
	if event.get("description"):
		description_parts.extend(["", event["description"]])
	body = {
		"summary": summary,
		"description": "\n".join(description_parts),
		"extendedProperties": {"private": {**SYNC_PRIVATE_PROPS, "syncDirection": ALLDAY_TO_TIMED, "sourceEventId": event.get("id") or "", "sourceCalendarId": source_calendar_id or ""}},
	}
	if parsed:
		start = datetime.combine(start_date, datetime.min.time()).replace(hour=parsed["hour"], minute=parsed["minute"])
		end = start + timedelta(minutes=parsed["duration_minutes"])
		body["start"] = {"dateTime": start.isoformat(), "timeZone": timezone_name}
		body["end"] = {"dateTime": end.isoformat(), "timeZone": timezone_name}
	else:
		body["start"] = {"date": start_date.isoformat()}
		body["end"] = {"date": end_date.isoformat()}
	if event.get("location"):
		body["location"] = event["location"]
	return body


def stable_event_hash(event_body):
	relevant = {key: event_body.get(key) for key in ("summary", "start", "end", "description", "location", "extendedProperties")}
	return hashlib.sha256(json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def list_timed_changes(service, pair: CalendarPair):
	events = []
	next_page_token = None
	full_sync = not pair.timed_sync_token
	while True:
		params = {"calendarId": pair.timed_calendar_id, "showDeleted": True, "singleEvents": True, "maxResults": 2500, "pageToken": next_page_token}
		if pair.timed_sync_token and not full_sync:
			params["syncToken"] = pair.timed_sync_token
		else:
			now = datetime.now(timezone.utc)
			params["timeMin"] = (now - timedelta(days=90)).isoformat()
			params["timeMax"] = (now + timedelta(days=365)).isoformat()
			params["orderBy"] = "startTime"
		try:
			response = service.events().list(**{key: value for key, value in params.items() if value is not None}).execute()
		except HttpError as exc:
			if exc.resp.status == 410 and pair.timed_sync_token:
				pair.timed_sync_token = None
				db_session.commit()
				return list_timed_changes(service, pair)
			raise
		events.extend(response.get("items", []))
		next_page_token = response.get("nextPageToken")
		if not next_page_token:
			return events, response.get("nextSyncToken")


def list_allday_changes(service, pair: CalendarPair):
	events = []
	next_page_token = None
	full_sync = not pair.allday_sync_token
	while True:
		params = {"calendarId": pair.allday_calendar_id, "showDeleted": True, "singleEvents": True, "maxResults": 2500, "pageToken": next_page_token}
		if pair.allday_sync_token and not full_sync:
			params["syncToken"] = pair.allday_sync_token
		else:
			now = datetime.now(timezone.utc)
			params["timeMin"] = (now - timedelta(days=90)).isoformat()
			params["timeMax"] = (now + timedelta(days=365)).isoformat()
			params["orderBy"] = "startTime"
		try:
			response = service.events().list(**{key: value for key, value in params.items() if value is not None}).execute()
		except HttpError as exc:
			if exc.resp.status == 410 and pair.allday_sync_token:
				pair.allday_sync_token = None
				db_session.commit()
				return list_allday_changes(service, pair)
			raise
		events.extend(response.get("items", []))
		next_page_token = response.get("nextPageToken")
		if not next_page_token:
			return events, response.get("nextSyncToken")


def find_existing_mirror(service, pair: CalendarPair, timed_event_id):
	response = service.events().list(calendarId=pair.allday_calendar_id, privateExtendedProperty=[f"calendarSyncApp=true", f"sourceEventId={timed_event_id}"], singleEvents=True, showDeleted=False, maxResults=10).execute()
	items = response.get("items", [])
	return items[0] if items else None


def find_existing_timed_mirror(service, pair: CalendarPair, allday_event_id):
	response = service.events().list(calendarId=pair.timed_calendar_id, privateExtendedProperty=[f"calendarSyncApp=true", f"sourceEventId={allday_event_id}"], singleEvents=True, showDeleted=False, maxResults=10).execute()
	items = response.get("items", [])
	return items[0] if items else None


def delete_mirror(service, pair: CalendarPair, mapping: EventMapping):
	try:
		service.events().delete(calendarId=pair.allday_calendar_id, eventId=mapping.allday_event_id).execute()
	except HttpError as exc:
		if exc.resp.status not in (404, 410):
			raise
	db_session.delete(mapping)


def delete_timed_mirror(service, pair: CalendarPair, mapping: EventMapping):
	try:
		service.events().delete(calendarId=pair.timed_calendar_id, eventId=mapping.timed_event_id).execute()
	except HttpError as exc:
		if exc.resp.status not in (404, 410):
			raise
	db_session.delete(mapping)


def record_conflict(pair, timed_event_id, allday_event_id, reason):
	db_session.add(Conflict(calendar_pair_id=pair.id, timed_event_id=timed_event_id, allday_event_id=allday_event_id, reason=reason))


def sync_timed_event(service, pair: CalendarPair, event):
	timed_event_id = event["id"]
	mapping = EventMapping.query.filter_by(calendar_pair_id=pair.id, timed_event_id=timed_event_id).one_or_none()

	if event.get("status") == "cancelled":
		if mapping:
			if mapping.status == ALLDAY_TO_TIMED:
				db_session.delete(mapping)
			else:
				delete_mirror(service, pair, mapping)
		return "deleted" if mapping else "skipped"

	if is_all_day_event(event) or is_sync_generated_from(event, pair.allday_calendar_id, ALLDAY_TO_TIMED):
		return "skipped"

	body = timed_event_to_allday_event(event, pair.timed_calendar_id)
	body_hash = stable_event_hash(body)

	if not mapping:
		existing = find_existing_mirror(service, pair, timed_event_id)
		if existing:
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=timed_event_id, allday_event_id=existing["id"])
			db_session.add(mapping)
		else:
			created = service.events().insert(calendarId=pair.allday_calendar_id, body=body).execute()
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=timed_event_id, allday_event_id=created["id"], allday_etag=created.get("etag"))
			db_session.add(mapping)
			mapping.timed_etag = event.get("etag")
			mapping.last_synced_hash = body_hash
			mapping.last_synced_at = utcnow()
			mapping.status = TIMED_TO_ALLDAY
			return "created"

	if mapping.last_synced_hash == body_hash and mapping.timed_etag == event.get("etag"):
		return "unchanged"

	try:
		current_allday = service.events().get(calendarId=pair.allday_calendar_id, eventId=mapping.allday_event_id).execute()
	except HttpError as exc:
		if exc.resp.status in (404, 410):
			created = service.events().insert(calendarId=pair.allday_calendar_id, body=body).execute()
			mapping.allday_event_id = created["id"]
			current_allday = created
		else:
			raise

	if mapping.allday_etag and current_allday.get("etag") != mapping.allday_etag:
		props = current_allday.get("extendedProperties", {}).get("private", {})
		if props.get("calendarSyncApp") != "true" or props.get("sourceEventId") != timed_event_id:
			record_conflict(pair, timed_event_id, mapping.allday_event_id, "Mapped all-day event was externally replaced or stripped of sync metadata.")
			return "conflict"

	updated = service.events().update(calendarId=pair.allday_calendar_id, eventId=mapping.allday_event_id, body=body).execute()
	mapping.timed_etag = event.get("etag")
	mapping.allday_etag = updated.get("etag")
	mapping.last_synced_hash = body_hash
	mapping.last_synced_at = utcnow()
	mapping.status = TIMED_TO_ALLDAY
	return "updated"


def get_calendar_timezone(service, calendar_id):
	try:
		return service.calendars().get(calendarId=calendar_id).execute().get("timeZone") or "UTC"
	except Exception:
		return "UTC"


def sync_allday_event(service, pair: CalendarPair, event, timezone_name):
	allday_event_id = event["id"]
	mapping = EventMapping.query.filter_by(calendar_pair_id=pair.id, allday_event_id=allday_event_id).one_or_none()

	if event.get("status") == "cancelled":
		if mapping:
			if mapping.status == TIMED_TO_ALLDAY:
				db_session.delete(mapping)
			else:
				delete_timed_mirror(service, pair, mapping)
		return "deleted" if mapping else "skipped"

	if not is_all_day_event(event) or is_sync_generated_from(event, pair.timed_calendar_id, TIMED_TO_ALLDAY):
		return "skipped"

	body = allday_event_to_timed_calendar_event(event, pair.allday_calendar_id, timezone_name)
	body_hash = stable_event_hash(body)

	if not mapping:
		existing = find_existing_timed_mirror(service, pair, allday_event_id)
		if existing:
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=existing["id"], allday_event_id=allday_event_id)
			db_session.add(mapping)
		else:
			created = service.events().insert(calendarId=pair.timed_calendar_id, body=body).execute()
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=created["id"], allday_event_id=allday_event_id, timed_etag=created.get("etag"))
			db_session.add(mapping)
			mapping.allday_etag = event.get("etag")
			mapping.last_synced_hash = body_hash
			mapping.last_synced_at = utcnow()
			mapping.status = ALLDAY_TO_TIMED
			return "created"

	if mapping.last_synced_hash == body_hash and mapping.allday_etag == event.get("etag"):
		return "unchanged"

	try:
		current_timed = service.events().get(calendarId=pair.timed_calendar_id, eventId=mapping.timed_event_id).execute()
	except HttpError as exc:
		if exc.resp.status in (404, 410):
			created = service.events().insert(calendarId=pair.timed_calendar_id, body=body).execute()
			mapping.timed_event_id = created["id"]
			current_timed = created
		else:
			raise

	if mapping.timed_etag and current_timed.get("etag") != mapping.timed_etag:
		props = sync_private_props(current_timed)
		if props.get("calendarSyncApp") != "true" or props.get("sourceEventId") != allday_event_id:
			record_conflict(pair, mapping.timed_event_id, allday_event_id, "Mapped timed event was externally replaced or stripped of sync metadata.")
			return "conflict"

	updated = service.events().update(calendarId=pair.timed_calendar_id, eventId=mapping.timed_event_id, body=body).execute()
	mapping.timed_etag = updated.get("etag")
	mapping.allday_etag = event.get("etag")
	mapping.last_synced_hash = body_hash
	mapping.last_synced_at = utcnow()
	mapping.status = ALLDAY_TO_TIMED
	return "updated"


def run_sync_for_pair(service, pair: CalendarPair):
	run = SyncRun(calendar_pair_id=pair.id, status="running")
	db_session.add(run)
	db_session.commit()
	counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0, "conflict": 0}
	try:
		events, next_sync_token = list_timed_changes(service, pair)
		for event in events:
			result = sync_timed_event(service, pair, event)
			counts[result] = counts.get(result, 0) + 1
		if next_sync_token:
			pair.timed_sync_token = next_sync_token
		timezone_name = get_calendar_timezone(service, pair.timed_calendar_id)
		events, next_sync_token = list_allday_changes(service, pair)
		for event in events:
			result = sync_allday_event(service, pair, event, timezone_name)
			counts[result] = counts.get(result, 0) + 1
		if next_sync_token:
			pair.allday_sync_token = next_sync_token
		run.status = "success"
		run.message = ", ".join(f"{value} {key}" for key, value in counts.items() if value)
	except Exception as exc:
		run.status = "error"
		run.message = str(exc)
		raise
	finally:
		run.finished_at = utcnow()
		db_session.commit()
	return run
