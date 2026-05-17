import re


TIME_TITLE_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+(.+)$", re.IGNORECASE)


def parse_allday_title_for_timed_event(title):
	"""Design placeholder for future all-day -> timed support. Returns parsed parts or None; callers should create conflicts instead of guessing."""
	match = TIME_TITLE_RE.match(title or "")
	if not match:
		return None
	hour_text, minute_text, meridiem, summary = match.groups()
	hour = int(hour_text)
	minute = int(minute_text or "0")
	if minute > 59:
		return None
	if meridiem:
		meridiem = meridiem.lower()
		if hour < 1 or hour > 12:
			return None
		if meridiem == "pm" and hour != 12:
			hour += 12
		if meridiem == "am" and hour == 12:
			hour = 0
	elif hour > 23:
		return None
	return {"hour": hour, "minute": minute, "summary": summary, "duration_minutes": 60}
