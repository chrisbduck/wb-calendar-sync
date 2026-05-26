import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from app.db import db_session
from app.future_sync import parse_allday_title_for_timed_event
from app.models import CalendarPair, Conflict, EventMapping, SyncRun, utcnow


SYNC_PRIVATE_PROPS = {"calendarSyncApp": "true"}
TIMED_TO_ALLDAY = "timed_to_allday"
ALLDAY_TO_TIMED = "allday_to_timed"
logger = logging.getLogger(__name__)


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


def convert_event_start_to_timezone(event, timezone_name=None):
	start = parse_google_datetime(event.get("start", {}))
	if start is None:
		raise ValueError("Timed event is missing a start dateTime")
	source_tz = event_timezone(event)
	target_tz = ZoneInfo(timezone_name) if timezone_name else source_tz
	if source_tz and not start.tzinfo:
		start = start.replace(tzinfo=source_tz)
	if target_tz and start.tzinfo:
		start = start.astimezone(target_tz)
	return start


def format_start_time(dt):
	hour = dt.hour % 12 or 12
	minute = f":{dt.minute:02d}" if dt.minute else ""
	suffix = "am" if dt.hour < 12 else "pm"
	return f"{hour}{minute}{suffix}"


def event_summary(event):
	return event.get("summary") or "(No title)"


def log_event_change(sync_logger, action, calendar_kind, calendar_id, event):
	if sync_logger:
		sync_logger.log_event_change(action, calendar_kind, calendar_id, event)


class SyncLogContext:
	def __init__(self, pair, calendar_names=None):
		self.pair = pair
		self.calendar_names = calendar_names or {}

	def calendar_name(self, calendar_id):
		return self.calendar_names.get(calendar_id) or calendar_id

	def log_event_change(self, action, calendar_kind, calendar_id, event):
		logger.info("Sync %s %s calendar event on %s: id=%s title=%r", action, calendar_kind, self.calendar_name(calendar_id), event.get("id"), event_summary(event))

	def log_summary(self, run, counts, timed_processed, allday_processed):
		logger.info("Sync summary for pair %s: status=%s processed=%s hourly, %s daily; created=%s, updated=%s, deleted=%s, unchanged=%s, skipped=%s, conflicts=%s", self.pair.id, run.status, timed_processed, allday_processed, counts.get("created", 0), counts.get("updated", 0), counts.get("deleted", 0), counts.get("unchanged", 0), counts.get("skipped", 0), counts.get("conflict", 0))


def calendar_display_name(calendar):
	return calendar.get("summaryOverride") or calendar.get("summary") or calendar.get("id")


def get_calendar_names(service, pair):
	try:
		calendars = service.calendarList().list().execute().get("items", [])
	except Exception:
		return {pair.timed_calendar_id: pair.timed_calendar_id, pair.allday_calendar_id: pair.allday_calendar_id}
	names = {calendar.get("id"): calendar_display_name(calendar) for calendar in calendars}
	return {pair.timed_calendar_id: names.get(pair.timed_calendar_id, pair.timed_calendar_id), pair.allday_calendar_id: names.get(pair.allday_calendar_id, pair.allday_calendar_id)}


def copy_core_fields(source, body):
	body["description"] = source.get("description") or ""
	body["location"] = source.get("location") or ""
	if source.get("conferenceData"):
		body["conferenceData"] = source["conferenceData"]
	else:
		body.pop("conferenceData", None)
	return body


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


def timed_event_to_allday_event(event, source_calendar_id=None, timezone_name=None):
	start = convert_event_start_to_timezone(event, timezone_name)
	date = start.date().isoformat()
	summary = event.get("summary") or "(No title)"
	body = {
		"summary": f"{format_start_time(start)} {summary}",
		"start": {"date": date},
		"end": {"date": (start.date() + timedelta(days=1)).isoformat()},
		"extendedProperties": {"private": {**SYNC_PRIVATE_PROPS, "syncDirection": TIMED_TO_ALLDAY, "sourceEventId": event.get("id") or "", "sourceCalendarId": source_calendar_id or ""}},
	}
	return copy_core_fields(event, body)


def allday_event_to_timed_calendar_event(event, source_calendar_id=None, timezone_name="UTC", existing_timed_event=None):
	if not is_all_day_event(event):
		raise ValueError("All-day source event is missing a start date")
	start_date = parse_google_datetime(event.get("start", {})).date()
	end_date = parse_google_datetime(event.get("end", {})).date() if event.get("end") else start_date + timedelta(days=1)
	parsed = parse_allday_title_for_timed_event(event.get("summary") or "")
	summary = (parsed or {}).get("summary") or event.get("summary") or "(No title)"
	body = {
		"summary": summary,
		"extendedProperties": {"private": {**SYNC_PRIVATE_PROPS, "syncDirection": ALLDAY_TO_TIMED, "sourceEventId": event.get("id") or "", "sourceCalendarId": source_calendar_id or ""}},
	}
	if parsed:
		start = datetime.combine(start_date, datetime.min.time()).replace(hour=parsed["hour"], minute=parsed["minute"])
		end = start + timedelta(minutes=parsed["duration_minutes"])
		body["start"] = {"dateTime": start.isoformat(), "timeZone": timezone_name}
		body["end"] = {"dateTime": end.isoformat(), "timeZone": timezone_name}
	elif existing_timed_event and existing_timed_event.get("start", {}).get("dateTime") and existing_timed_event.get("end", {}).get("dateTime"):
		body["start"] = existing_timed_event["start"]
		body["end"] = existing_timed_event["end"]
	else:
		body["start"] = {"date": start_date.isoformat()}
		body["end"] = {"date": end_date.isoformat()}
	return copy_core_fields(event, body)


def stable_event_hash(event_body):
	relevant = {key: event_body.get(key) for key in ("summary", "start", "end", "description", "location", "conferenceData", "extendedProperties")}
	return hashlib.sha256(json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def parse_created_at(event):
	created = event.get("created")
	if not created:
		return None
	return parse_google_datetime({"dateTime": created})


def timed_event_is_original(mapping, timed_event=None, allday_event=None):
	timed_created = parse_created_at(timed_event or {})
	allday_created = parse_created_at(allday_event or {})
	if timed_created and allday_created:
		return timed_created <= allday_created
	return mapping.status != ALLDAY_TO_TIMED


def get_event_or_none(service, calendar_id, event_id):
	try:
		return service.events().get(calendarId=calendar_id, eventId=event_id).execute()
	except HttpError as exc:
		if exc.resp.status in (404, 410):
			return None
		raise


def event_is_deleted(event):
	return event is None or event.get("status") == "cancelled"


def insert_event(service, calendar_id, body):
	return service.events().insert(calendarId=calendar_id, body=body, conferenceDataVersion=1, sendUpdates="none").execute()


def update_event(service, calendar_id, event_id, body):
	return service.events().update(calendarId=calendar_id, eventId=event_id, body=body, conferenceDataVersion=1, sendUpdates="none").execute()


def record_sync_state(mapping, timed_event, allday_event, body_hash, status):
	mapping.timed_etag = timed_event.get("etag")
	mapping.allday_etag = allday_event.get("etag")
	mapping.last_synced_hash = body_hash
	mapping.last_synced_at = utcnow()
	mapping.status = status


def preserve_target_extended_properties(body, target_event):
	if target_event and target_event.get("extendedProperties"):
		body["extendedProperties"] = target_event["extendedProperties"]
	else:
		body.pop("extendedProperties", None)
	return body


def original_status(mapping, timed_event, allday_event):
	return TIMED_TO_ALLDAY if timed_event_is_original(mapping, timed_event, allday_event) else ALLDAY_TO_TIMED


def events_have_same_synced_fields(timed_event, allday_event, timezone_name):
	expected_allday = timed_event_to_allday_event(timed_event, timezone_name=timezone_name)
	expected_timed = allday_event_to_timed_calendar_event(allday_event, timezone_name=timezone_name, existing_timed_event=timed_event)
	fields = ("summary", "start", "end", "description", "location", "conferenceData")
	return all(expected_allday.get(field) == allday_event.get(field) for field in fields) and all(expected_timed.get(field) == timed_event.get(field) for field in fields)


def record_compatible_changed_events(mapping, timed_event, allday_event, timezone_name):
	body = timed_event_to_allday_event(timed_event, timezone_name=timezone_name)
	preserve_target_extended_properties(body, allday_event)
	record_sync_state(mapping, timed_event, allday_event, stable_event_hash(body), original_status(mapping, timed_event, allday_event))
	return "updated"


def update_allday_from_timed(service, pair, mapping, timed_event, timezone_name, allday_event=None, sync_logger=None):
	body = timed_event_to_allday_event(timed_event, pair.timed_calendar_id, timezone_name)
	preserve_target_extended_properties(body, allday_event)
	updated = update_event(service, pair.allday_calendar_id, mapping.allday_event_id, body)
	record_sync_state(mapping, timed_event, updated, stable_event_hash(body), original_status(mapping, timed_event, updated))
	log_event_change(sync_logger, "updated", "daily", pair.allday_calendar_id, updated)
	return "updated"


def update_timed_from_allday(service, pair, mapping, allday_event, timezone_name, timed_event=None, sync_logger=None):
	body = allday_event_to_timed_calendar_event(allday_event, pair.allday_calendar_id, timezone_name, timed_event)
	preserve_target_extended_properties(body, timed_event)
	updated = update_event(service, pair.timed_calendar_id, mapping.timed_event_id, body)
	record_sync_state(mapping, updated, allday_event, stable_event_hash(body), original_status(mapping, updated, allday_event))
	log_event_change(sync_logger, "updated", "hourly", pair.timed_calendar_id, updated)
	return "updated"


def recreate_timed_from_allday(service, pair, mapping, allday_event, timezone_name, sync_logger=None):
	body = allday_event_to_timed_calendar_event(allday_event, pair.allday_calendar_id, timezone_name)
	created = insert_event(service, pair.timed_calendar_id, body)
	mapping.timed_event_id = created["id"]
	record_sync_state(mapping, created, allday_event, stable_event_hash(body), ALLDAY_TO_TIMED)
	log_event_change(sync_logger, "created", "hourly", pair.timed_calendar_id, created)
	return "created"


def recreate_allday_from_timed(service, pair, mapping, timed_event, timezone_name, sync_logger=None):
	body = timed_event_to_allday_event(timed_event, pair.timed_calendar_id, timezone_name)
	created = insert_event(service, pair.allday_calendar_id, body)
	mapping.allday_event_id = created["id"]
	record_sync_state(mapping, timed_event, created, stable_event_hash(body), TIMED_TO_ALLDAY)
	log_event_change(sync_logger, "created", "daily", pair.allday_calendar_id, created)
	return "created"


def start_of_today(timezone_name):
	tz = ZoneInfo(timezone_name)
	now = datetime.now(tz)
	return datetime(now.year, now.month, now.day, tzinfo=tz)


def event_starts_before_sync_cutoff(event, timezone_name):
	start = parse_google_datetime(event.get("start", {}))
	if start is None:
		return False
	cutoff = query_start_for_overlapping_events(timezone_name).date()
	if is_all_day_event(event):
		return start.date() < cutoff
	if start.tzinfo:
		start = start.astimezone(ZoneInfo(timezone_name))
	return start.date() < cutoff


def query_start_for_overlapping_events(timezone_name):
	return start_of_today(timezone_name) - timedelta(days=7)


def event_starts_before_today(event, timezone_name):
	start = parse_google_datetime(event.get("start", {}))
	if start is None:
		return False
	today = start_of_today(timezone_name).date()
	if is_all_day_event(event):
		return start.date() < today
	if start.tzinfo:
		start = start.astimezone(ZoneInfo(timezone_name))
	return start.date() < today


def list_timed_changes(service, pair: CalendarPair, timezone_name):
	events = []
	next_page_token = None
	full_sync = not pair.timed_sync_token
	while True:
		params = {"calendarId": pair.timed_calendar_id, "showDeleted": True, "singleEvents": True, "maxResults": 2500, "pageToken": next_page_token}
		if pair.timed_sync_token and not full_sync:
			params["syncToken"] = pair.timed_sync_token
		else:
			today = start_of_today(timezone_name)
			params["timeMin"] = query_start_for_overlapping_events(timezone_name).isoformat()
			params["timeMax"] = (today + timedelta(days=365)).isoformat()
			params["orderBy"] = "startTime"
		try:
			response = service.events().list(**{key: value for key, value in params.items() if value is not None}).execute()
		except HttpError as exc:
			if exc.resp.status == 410 and pair.timed_sync_token:
				pair.timed_sync_token = None
				db_session.commit()
				return list_timed_changes(service, pair, timezone_name)
			raise
		events.extend(response.get("items", []))
		next_page_token = response.get("nextPageToken")
		if not next_page_token:
			return events, response.get("nextSyncToken")


def list_allday_changes(service, pair: CalendarPair, timezone_name):
	events = []
	next_page_token = None
	full_sync = not pair.allday_sync_token
	while True:
		params = {"calendarId": pair.allday_calendar_id, "showDeleted": True, "singleEvents": True, "maxResults": 2500, "pageToken": next_page_token}
		if pair.allday_sync_token and not full_sync:
			params["syncToken"] = pair.allday_sync_token
		else:
			today = start_of_today(timezone_name)
			params["timeMin"] = query_start_for_overlapping_events(timezone_name).isoformat()
			params["timeMax"] = (today + timedelta(days=365)).isoformat()
			params["orderBy"] = "startTime"
		try:
			response = service.events().list(**{key: value for key, value in params.items() if value is not None}).execute()
		except HttpError as exc:
			if exc.resp.status == 410 and pair.allday_sync_token:
				pair.allday_sync_token = None
				db_session.commit()
				return list_allday_changes(service, pair, timezone_name)
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


def delete_mirror(service, pair: CalendarPair, mapping: EventMapping, sync_logger=None):
	deleted_event = get_event_or_none(service, pair.allday_calendar_id, mapping.allday_event_id) or {"id": mapping.allday_event_id}
	try:
		service.events().delete(calendarId=pair.allday_calendar_id, eventId=mapping.allday_event_id, sendUpdates="none").execute()
	except HttpError as exc:
		if exc.resp.status not in (404, 410):
			raise
	log_event_change(sync_logger, "deleted", "daily", pair.allday_calendar_id, deleted_event)
	db_session.delete(mapping)
	db_session.flush()


def delete_timed_mirror(service, pair: CalendarPair, mapping: EventMapping, sync_logger=None):
	deleted_event = get_event_or_none(service, pair.timed_calendar_id, mapping.timed_event_id) or {"id": mapping.timed_event_id}
	try:
		service.events().delete(calendarId=pair.timed_calendar_id, eventId=mapping.timed_event_id, sendUpdates="none").execute()
	except HttpError as exc:
		if exc.resp.status not in (404, 410):
			raise
	log_event_change(sync_logger, "deleted", "hourly", pair.timed_calendar_id, deleted_event)
	db_session.delete(mapping)
	db_session.flush()


def record_conflict(pair, timed_event_id, allday_event_id, reason):
	db_session.add(Conflict(calendar_pair_id=pair.id, timed_event_id=timed_event_id, allday_event_id=allday_event_id, reason=reason))


def clear_deleted_event_mappings(service, pair: CalendarPair):
	mappings = EventMapping.query.filter_by(calendar_pair_id=pair.id).all()
	result = {"checked": 0, "cleared": 0, "kept": 0}
	for mapping in mappings:
		result["checked"] += 1
		timed_event = get_event_or_none(service, pair.timed_calendar_id, mapping.timed_event_id)
		allday_event = get_event_or_none(service, pair.allday_calendar_id, mapping.allday_event_id)
		if event_is_deleted(timed_event) and event_is_deleted(allday_event):
			db_session.delete(mapping)
			result["cleared"] += 1
		else:
			result["kept"] += 1
	db_session.commit()
	return result


def sync_mapped_pair_from_timed(service, pair, mapping, timed_event, timezone_name, sync_logger=None):
	current_timed = get_event_or_none(service, pair.timed_calendar_id, mapping.timed_event_id) or timed_event
	current_allday = get_event_or_none(service, pair.allday_calendar_id, mapping.allday_event_id)
	if not current_allday:
		return recreate_allday_from_timed(service, pair, mapping, current_timed, timezone_name, sync_logger)

	timed_changed = current_timed.get("etag") != mapping.timed_etag
	allday_changed = current_allday.get("etag") != mapping.allday_etag
	if not timed_changed and not allday_changed:
		return "unchanged"
	if timed_changed and allday_changed:
		if events_have_same_synced_fields(current_timed, current_allday, timezone_name):
			return record_compatible_changed_events(mapping, current_timed, current_allday, timezone_name)
		record_conflict(pair, mapping.timed_event_id, mapping.allday_event_id, "Both mapped events changed before sync; earlier-created event won.")
		if timed_event_is_original(mapping, current_timed, current_allday):
			return update_allday_from_timed(service, pair, mapping, current_timed, timezone_name, current_allday, sync_logger)
		return update_timed_from_allday(service, pair, mapping, current_allday, timezone_name, current_timed, sync_logger)
	if timed_changed:
		return update_allday_from_timed(service, pair, mapping, current_timed, timezone_name, current_allday, sync_logger)
	return update_timed_from_allday(service, pair, mapping, current_allday, timezone_name, current_timed, sync_logger)


def sync_mapped_pair_from_allday(service, pair, mapping, allday_event, timezone_name, sync_logger=None):
	current_allday = get_event_or_none(service, pair.allday_calendar_id, mapping.allday_event_id) or allday_event
	current_timed = get_event_or_none(service, pair.timed_calendar_id, mapping.timed_event_id)
	if not current_timed:
		return recreate_timed_from_allday(service, pair, mapping, current_allday, timezone_name, sync_logger)

	timed_changed = current_timed.get("etag") != mapping.timed_etag
	allday_changed = current_allday.get("etag") != mapping.allday_etag
	if not timed_changed and not allday_changed:
		return "unchanged"
	if timed_changed and allday_changed:
		if events_have_same_synced_fields(current_timed, current_allday, timezone_name):
			return record_compatible_changed_events(mapping, current_timed, current_allday, timezone_name)
		record_conflict(pair, mapping.timed_event_id, mapping.allday_event_id, "Both mapped events changed before sync; earlier-created event won.")
		if timed_event_is_original(mapping, current_timed, current_allday):
			return update_allday_from_timed(service, pair, mapping, current_timed, timezone_name, current_allday, sync_logger)
		return update_timed_from_allday(service, pair, mapping, current_allday, timezone_name, current_timed, sync_logger)
	if timed_changed:
		return update_allday_from_timed(service, pair, mapping, current_timed, timezone_name, current_allday, sync_logger)
	return update_timed_from_allday(service, pair, mapping, current_allday, timezone_name, current_timed, sync_logger)


def sync_timed_event(service, pair: CalendarPair, event, timezone_name, sync_logger=None):
	timed_event_id = event["id"]
	mapping = EventMapping.query.filter_by(calendar_pair_id=pair.id, timed_event_id=timed_event_id).one_or_none()

	if event.get("status") == "cancelled":
		if mapping:
			allday_event = get_event_or_none(service, pair.allday_calendar_id, mapping.allday_event_id)
			if event_is_deleted(allday_event):
				db_session.delete(mapping)
				return "ignored_deleted"
			if timed_event_is_original(mapping, event, allday_event):
				delete_mirror(service, pair, mapping, sync_logger)
			elif allday_event:
				return recreate_timed_from_allday(service, pair, mapping, allday_event, timezone_name, sync_logger)
			else:
				db_session.delete(mapping)
		return "deleted" if mapping else "ignored_deleted"

	if event_starts_before_sync_cutoff(event, timezone_name):
		return "skipped"

	if mapping:
		return sync_mapped_pair_from_timed(service, pair, mapping, event, timezone_name, sync_logger)

	if is_all_day_event(event) or is_sync_generated_from(event, pair.allday_calendar_id, ALLDAY_TO_TIMED):
		return "skipped"

	body = timed_event_to_allday_event(event, pair.timed_calendar_id, timezone_name)
	body_hash = stable_event_hash(body)

	if not mapping:
		existing = find_existing_mirror(service, pair, timed_event_id)
		if existing:
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=timed_event_id, allday_event_id=existing["id"])
			db_session.add(mapping)
		else:
			created = insert_event(service, pair.allday_calendar_id, body)
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=timed_event_id, allday_event_id=created["id"], allday_etag=created.get("etag"))
			db_session.add(mapping)
			record_sync_state(mapping, event, created, body_hash, TIMED_TO_ALLDAY)
			log_event_change(sync_logger, "created", "daily", pair.allday_calendar_id, created)
			return "created"

	if mapping.last_synced_hash == body_hash and mapping.timed_etag == event.get("etag"):
		return "unchanged"

	try:
		current_allday = service.events().get(calendarId=pair.allday_calendar_id, eventId=mapping.allday_event_id).execute()
	except HttpError as exc:
		if exc.resp.status in (404, 410):
			created = insert_event(service, pair.allday_calendar_id, body)
			mapping.allday_event_id = created["id"]
			current_allday = created
		else:
			raise

	if mapping.allday_etag and current_allday.get("etag") != mapping.allday_etag:
		props = current_allday.get("extendedProperties", {}).get("private", {})
		if props.get("calendarSyncApp") != "true" or props.get("sourceEventId") != timed_event_id:
			record_conflict(pair, timed_event_id, mapping.allday_event_id, "Mapped all-day event was externally replaced or stripped of sync metadata.")
			return "conflict"

	updated = update_event(service, pair.allday_calendar_id, mapping.allday_event_id, body)
	record_sync_state(mapping, event, updated, body_hash, TIMED_TO_ALLDAY)
	log_event_change(sync_logger, "updated", "daily", pair.allday_calendar_id, updated)
	return "updated"


def get_calendar_timezone(service, calendar_id):
	try:
		return service.calendars().get(calendarId=calendar_id).execute().get("timeZone") or "UTC"
	except Exception:
		return "UTC"


def sync_allday_event(service, pair: CalendarPair, event, timezone_name, sync_logger=None):
	allday_event_id = event["id"]
	mapping = EventMapping.query.filter_by(calendar_pair_id=pair.id, allday_event_id=allday_event_id).one_or_none()

	if event.get("status") == "cancelled":
		if mapping:
			timed_event = get_event_or_none(service, pair.timed_calendar_id, mapping.timed_event_id)
			if event_is_deleted(timed_event):
				db_session.delete(mapping)
				return "ignored_deleted"
			if not timed_event_is_original(mapping, timed_event, event):
				delete_timed_mirror(service, pair, mapping, sync_logger)
			elif timed_event:
				return recreate_allday_from_timed(service, pair, mapping, timed_event, timezone_name, sync_logger)
			else:
				db_session.delete(mapping)
		return "deleted" if mapping else "ignored_deleted"

	if event_starts_before_sync_cutoff(event, timezone_name):
		return "skipped"

	if mapping:
		return sync_mapped_pair_from_allday(service, pair, mapping, event, timezone_name, sync_logger)

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
			created = insert_event(service, pair.timed_calendar_id, body)
			mapping = EventMapping(calendar_pair_id=pair.id, timed_event_id=created["id"], allday_event_id=allday_event_id, timed_etag=created.get("etag"))
			db_session.add(mapping)
			record_sync_state(mapping, created, event, body_hash, ALLDAY_TO_TIMED)
			log_event_change(sync_logger, "created", "hourly", pair.timed_calendar_id, created)
			return "created"

	if mapping.last_synced_hash == body_hash and mapping.allday_etag == event.get("etag"):
		return "unchanged"

	try:
		current_timed = service.events().get(calendarId=pair.timed_calendar_id, eventId=mapping.timed_event_id).execute()
	except HttpError as exc:
		if exc.resp.status in (404, 410):
			created = insert_event(service, pair.timed_calendar_id, body)
			mapping.timed_event_id = created["id"]
			current_timed = created
		else:
			raise

	if mapping.timed_etag and current_timed.get("etag") != mapping.timed_etag:
		props = sync_private_props(current_timed)
		if props.get("calendarSyncApp") != "true" or props.get("sourceEventId") != allday_event_id:
			record_conflict(pair, mapping.timed_event_id, allday_event_id, "Mapped timed event was externally replaced or stripped of sync metadata.")
			return "conflict"

	updated = update_event(service, pair.timed_calendar_id, mapping.timed_event_id, body)
	record_sync_state(mapping, updated, event, body_hash, ALLDAY_TO_TIMED)
	log_event_change(sync_logger, "updated", "hourly", pair.timed_calendar_id, updated)
	return "updated"


def run_sync_for_pair(service, pair: CalendarPair):
	run = SyncRun(calendar_pair_id=pair.id, status="running")
	db_session.add(run)
	db_session.commit()
	counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0, "ignored_deleted": 0, "conflict": 0}
	timed_processed = 0
	allday_processed = 0
	sync_logger = SyncLogContext(pair, get_calendar_names(service, pair))
	try:
		timezone_name = get_calendar_timezone(service, pair.timed_calendar_id)
		events, next_sync_token = list_timed_changes(service, pair, timezone_name)
		timed_processed = len(events)
		for event in events:
			result = sync_timed_event(service, pair, event, timezone_name, sync_logger)
			counts[result] = counts.get(result, 0) + 1
		if next_sync_token:
			pair.timed_sync_token = next_sync_token
		events, next_sync_token = list_allday_changes(service, pair, timezone_name)
		allday_processed = len(events)
		for event in events:
			result = sync_allday_event(service, pair, event, timezone_name, sync_logger)
			counts[result] = counts.get(result, 0) + 1
		if next_sync_token:
			pair.allday_sync_token = next_sync_token
		run.status = "success"
		run.message = ", ".join(f"{value} {key}" for key, value in counts.items() if value and key != "ignored_deleted")
	except Exception as exc:
		run.status = "error"
		run.message = str(exc)
		raise
	finally:
		run.finished_at = utcnow()
		db_session.commit()
		sync_logger.log_summary(run, counts, timed_processed, allday_processed)
	return run
