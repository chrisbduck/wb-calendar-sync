import re


TIME_RANGE_RE = re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|–|—|\bto\b)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?![a-z0-9])", re.IGNORECASE)
TIME_RE = re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?![a-z0-9])|(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", re.IGNORECASE)
TIME_PARTICLE_RE = re.compile(r"\b(?:at|from|around|about|by)\s*$", re.IGNORECASE)


def clean_time_from_title(title, match):
	prefix = TIME_PARTICLE_RE.sub("", title[:match.start()]).strip()
	cleaned = f"{prefix} {title[match.end():]}".strip()
	return re.sub(r"\s+", " ", cleaned.strip(" -–—,:;")).strip() or title.strip()


def parse_clock(hour_text, minute_text=None, meridiem=None, inferred_meridiem=None):
	hour = int(hour_text)
	minute = int(minute_text or "0")
	if minute > 59:
		return None
	meridiem = (meridiem or inferred_meridiem or "").lower()
	if meridiem:
		if hour < 1 or hour > 12:
			return None
		if meridiem == "pm" and hour != 12:
			hour += 12
		if meridiem == "am" and hour == 12:
			hour = 0
	elif hour > 23:
		return None
	return hour, minute


def minutes_after_midnight(clock):
	return clock[0] * 60 + clock[1]


def parse_allday_title_for_timed_event(title):
	"""Return parsed time data for all-day -> timed sync, or None when there is no clear time."""
	title = title or ""
	range_match = TIME_RANGE_RE.search(title)
	if range_match:
		start_hour, start_minute, start_meridiem, end_hour, end_minute, end_meridiem = range_match.groups()
		if not start_meridiem and not end_meridiem and not start_minute and not end_minute:
			return None
		inferred_start_meridiem = end_meridiem if not start_meridiem and end_meridiem else None
		inferred_end_meridiem = start_meridiem if not end_meridiem and start_meridiem else None
		start_clock = parse_clock(start_hour, start_minute, start_meridiem, inferred_start_meridiem)
		end_clock = parse_clock(end_hour, end_minute, end_meridiem, inferred_end_meridiem)
		if not start_clock or not end_clock:
			return None
		start_minutes = minutes_after_midnight(start_clock)
		end_minutes = minutes_after_midnight(end_clock)
		if end_minutes <= start_minutes:
			end_minutes += 24 * 60
		return {"hour": start_clock[0], "minute": start_clock[1], "summary": clean_time_from_title(title, range_match), "duration_minutes": end_minutes - start_minutes}

	match = TIME_RE.search(title)
	if not match:
		return None
	if match.group(1):
		clock = parse_clock(match.group(1), match.group(2), match.group(3))
	else:
		clock = parse_clock(match.group(4), match.group(5))
	if not clock:
		return None
	return {"hour": clock[0], "minute": clock[1], "summary": clean_time_from_title(title, match), "duration_minutes": 60}
