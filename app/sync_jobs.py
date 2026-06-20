from googleapiclient.discovery import build

from app.db import db_session
from app.google_client import GoogleReconnectRequiredError, credentials_from_token
from app.models import CalendarPair, OAuthToken, SyncJob, utcnow
from app.sync import run_sync_for_pair


def get_or_create_calendar_pair(user_id, source_calendar_id, target_calendar_id, backup_calendar_id):
	pair = CalendarPair.query.filter_by(user_id=user_id, timed_calendar_id=source_calendar_id, allday_calendar_id=target_calendar_id, backup_calendar_id=backup_calendar_id).one_or_none()
	if pair:
		return pair
	pair = CalendarPair(user_id=user_id, timed_calendar_id=source_calendar_id, allday_calendar_id=target_calendar_id, backup_calendar_id=backup_calendar_id)
	db_session.add(pair)
	db_session.flush()
	return pair


def calendar_pair_matches_job(pair: CalendarPair | None, job: SyncJob):
	return bool(pair and pair.user_id == job.user_id and pair.timed_calendar_id == job.source_calendar_id and pair.allday_calendar_id == job.target_calendar_id and pair.backup_calendar_id == job.backup_calendar_id)


def calendar_pair_for_job(job):
	pair = job.calendar_pair
	if calendar_pair_matches_job(pair, job):
		return pair
	if not job.user_id:
		return None
	if not job.backup_calendar_id:
		return None
	pair = get_or_create_calendar_pair(job.user_id, job.source_calendar_id, job.target_calendar_id, job.backup_calendar_id)
	job.calendar_pair_id = pair.id
	db_session.commit()
	return pair


def run_sync_job(job: SyncJob):
	"""
	Run one calendar sync job.

	This reuses the existing calendar-pair sync engine and records job-level
	status so cron and manual runs can report the last outcome.
	"""
	job.last_run_at = utcnow()
	job.last_status = "skipped"
	job.last_error = None
	db_session.commit()

	if not job.enabled:
		job.last_status = "skipped"
		job.last_error = "Job is disabled."
		db_session.commit()
		return {"id": job.id, "friendly_name": job.friendly_name, "status": job.last_status, "error": job.last_error}

	token = OAuthToken.query.filter_by(user_id=job.user_id).one_or_none() if job.user_id else None
	if not token:
		job.last_status = "skipped"
		job.last_error = "No Google OAuth token is available for this sync job."
		db_session.commit()
		return {"id": job.id, "friendly_name": job.friendly_name, "status": job.last_status, "error": job.last_error}

	pair = calendar_pair_for_job(job)

	if not pair:
		job.last_status = "skipped"
		job.last_error = "No calendar pair is available for this sync job."
		db_session.commit()
		return {"id": job.id, "friendly_name": job.friendly_name, "status": job.last_status, "error": job.last_error}

	try:
		service = build("calendar", "v3", credentials=credentials_from_token(token), cache_discovery=False)
		run = run_sync_for_pair(service, pair)
		job.last_status = "success" if run.status == "success" else "failed"
		job.last_error = None if run.status == "success" else run.message
		db_session.commit()
		return {"id": job.id, "friendly_name": job.friendly_name, "status": job.last_status, "error": job.last_error, "run_id": run.id, "message": run.message}
	except GoogleReconnectRequiredError as exc:
		job.last_status = "failed"
		job.last_error = str(exc)
		db_session.commit()
		return {"id": job.id, "friendly_name": job.friendly_name, "status": job.last_status, "error": job.last_error, "needs_reconnect": True}
	except Exception as exc:
		job.last_status = "failed"
		job.last_error = str(exc)
		db_session.commit()
		return {"id": job.id, "friendly_name": job.friendly_name, "status": job.last_status, "error": job.last_error}


def run_all_enabled_sync_jobs(user_id=None):
	"""
	Load all enabled sync jobs and run them one by one.
	"""
	query = SyncJob.query.filter_by(enabled=True)
	if user_id is not None:
		query = query.filter_by(user_id=user_id)
	jobs = query.order_by(SyncJob.created_at.asc()).all()
	results = [run_sync_job(job) for job in jobs]
	return {
		"total": len(results),
		"succeeded": len([result for result in results if result["status"] == "success"]),
		"failed": len([result for result in results if result["status"] == "failed"]),
		"results": results,
	}
