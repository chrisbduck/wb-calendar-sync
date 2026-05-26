import os
from functools import wraps
from datetime import timezone
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlencode

from flask import Blueprint, abort, jsonify, redirect, request, send_from_directory, session

from app.db import db_session
from app.config import GOOGLE_SCOPES
from app.google_client import convert_expiry_for_database, current_calendar_service, current_user, make_flow, missing_required_scopes, userinfo_service
from app.models import CalendarPair, Conflict, OAuthToken, SyncJob, SyncRun, User
from app.sync import clear_deleted_event_mappings, run_sync_for_pair
from app.sync_jobs import get_or_create_calendar_pair, run_all_enabled_sync_jobs


bp = Blueprint("main", __name__)
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
RequiredValue = TypeVar("RequiredValue")


def login_required(view):
	@wraps(view)
	def wrapped(*args, **kwargs):
		if not current_user():
			if request.path.startswith("/api/"):
				return jsonify({"error": "auth_required"}), 401
			return redirect(frontend_url("/"))
		return view(*args, **kwargs)
	return wrapped


def active_pair(user):
	return CalendarPair.query.filter_by(user_id=user.id).order_by(CalendarPair.created_at.desc()).first()


def editable_pair_for_setup(user, timed_calendar_id, allday_calendar_id):
	pair = active_pair(user)
	if pair is None:
		pair = CalendarPair(user_id=user.id)
		db_session.add(pair)
		return pair
	if pair.timed_calendar_id == timed_calendar_id and pair.allday_calendar_id == allday_calendar_id:
		return pair
	if pair.sync_jobs:
		pair = CalendarPair(user_id=user.id)
		db_session.add(pair)
	return pair


def calendar_display_name(calendar):
	return calendar.get("summaryOverride") or calendar.get("summary") or calendar.get("id")


def calendar_options(calendars):
	name_counts = {}
	for calendar in calendars:
		name = calendar_display_name(calendar)
		name_counts[name] = name_counts.get(name, 0) + 1
	options = []
	for calendar in calendars:
		name = calendar_display_name(calendar)
		label = f"{name} — {calendar.get('id')}" if name_counts[name] > 1 else name
		options.append({"id": calendar.get("id"), "name": name, "label": label})
	return options


def selected_calendar_names(service, pair):
	if not service or not pair:
		return {}
	try:
		calendars = service.calendarList().list().execute().get("items", [])
	except Exception:
		return {}
	return {calendar.get("id"): calendar_display_name(calendar) for calendar in calendars}


def serialize_datetime(value):
	if not value:
		return None
	if value.tzinfo is None:
		value = value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def serialize_run(run):
	return {"id": run.id, "started_at": serialize_datetime(run.started_at), "finished_at": serialize_datetime(run.finished_at), "status": run.status, "message": run.message}


def serialize_conflict(conflict):
	return {"id": conflict.id, "created_at": serialize_datetime(conflict.created_at), "resolved_at": serialize_datetime(conflict.resolved_at), "timed_event_id": conflict.timed_event_id, "allday_event_id": conflict.allday_event_id, "reason": conflict.reason}


def serialize_sync_job(job):
	return {
		"id": job.id,
		"friendly_name": job.friendly_name,
		"source_calendar_id": job.source_calendar_id,
		"target_calendar_id": job.target_calendar_id,
		"enabled": job.enabled,
		"created_at": serialize_datetime(job.created_at),
		"updated_at": serialize_datetime(job.updated_at),
		"last_run_at": serialize_datetime(job.last_run_at),
		"last_status": job.last_status,
		"last_error": job.last_error,
	}


def no_store_json(body, status=200):
	response = jsonify(body)
	response.status_code = status
	response.headers["Cache-Control"] = "no-store, max-age=0"
	return response


def require_google_value(value: RequiredValue | None, name: str) -> RequiredValue:
	if not value:
		raise RuntimeError(f"Google OAuth response did not include {name}.")
	return value


def frontend_url(path="/", **query):
	import os
	base_url = os.environ.get("FRONTEND_BASE_URL", "").rstrip("/")
	if not path.startswith("/"):
		path = f"/{path}"
	url = f"{base_url}{path}" if base_url else path
	clean_query = {key: value for key, value in query.items() if value is not None}
	if clean_query:
		url = f"{url}?{urlencode(clean_query)}"
	return url


def serve_frontend():
	if FRONTEND_DIST.exists():
		response = send_from_directory(FRONTEND_DIST, "index.html")
		response.headers["Cache-Control"] = "no-store, max-age=0"
		return response
	return "<!doctype html><title>WB Calendar Sync</title><div id=\"root\">Frontend build missing. Run npm run build.</div>", 500


@bp.get("/")
def index():
	return serve_frontend()


@bp.get("/health")
def health():
	return jsonify({"status": "ok"})


@bp.get("/auth/start")
def auth_start():
	flow = make_flow()
	auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="false", prompt="consent")
	session["oauth_state"] = state
	return redirect(auth_url)


@bp.get("/auth/callback")
def auth_callback():
	flow = make_flow(session.get("oauth_state"))
	flow.fetch_token(authorization_response=request.url)
	credentials = flow.credentials
	missing_scopes = missing_required_scopes(credentials.scopes)
	if missing_scopes:
		return redirect(frontend_url("/", message="Google sign-in worked, but Google did not grant Calendar access. Make sure Google Calendar API is enabled and the calendar scope is on the OAuth consent screen."))
	profile = userinfo_service(credentials).userinfo().get().execute()
	user = User.query.filter_by(google_sub=profile["id"]).one_or_none()
	if user is None:
		user = User()
		user.email = profile["email"]
		user.google_sub = profile["id"]
		db_session.add(user)
		db_session.flush()
	token = OAuthToken.query.filter_by(user_id=user.id).one_or_none()
	if token is None:
		token = OAuthToken()
		token.user_id = user.id
		db_session.add(token)
	token.access_token = require_google_value(credentials.token, "access token")
	token.refresh_token = credentials.refresh_token or token.refresh_token
	token.token_uri = "https://oauth2.googleapis.com/token"
	token.client_id = require_google_value(os.environ.get("GOOGLE_CLIENT_ID"), "client ID")
	token.client_secret = require_google_value(os.environ.get("GOOGLE_CLIENT_SECRET"), "client secret")
	token.scopes = " ".join(credentials.scopes or GOOGLE_SCOPES)
	token.expiry = convert_expiry_for_database(credentials.expiry)
	db_session.commit()
	session["user_id"] = user.id
	return redirect(frontend_url("/setup", message="Signed in with Google."))


@bp.get("/logout")
def logout():
	session.clear()
	return redirect(frontend_url("/", message="Signed out."))


@bp.get("/api/app-state")
def app_state():
	user = current_user()
	pair = active_pair(user) if user else None
	service = current_calendar_service() if user and pair else None
	calendar_names = selected_calendar_names(service, pair)
	runs = SyncRun.query.filter_by(calendar_pair_id=pair.id).order_by(SyncRun.started_at.desc()).limit(5).all() if pair else []
	last_success = next((run for run in runs if run.status == "success"), runs[0] if runs else None)
	return jsonify({
		"user": {"id": user.id, "email": user.email} if user else None,
		"pair": {"id": pair.id, "timed_calendar_id": pair.timed_calendar_id, "allday_calendar_id": pair.allday_calendar_id, "timed_calendar_name": calendar_names.get(pair.timed_calendar_id, pair.timed_calendar_id), "allday_calendar_name": calendar_names.get(pair.allday_calendar_id, pair.allday_calendar_id)} if pair else None,
		"recent_runs": [serialize_run(run) for run in runs],
		"last_synced_at": serialize_datetime(last_success.finished_at) if last_success and last_success.finished_at else None,
	})


@bp.get("/api/calendars")
@login_required
def api_calendars():
	user = current_user()
	service = current_calendar_service()
	if user is None or service is None:
		return jsonify({"error": "auth_required"}), 401
	pair = active_pair(user)
	calendars = service.calendarList().list().execute().get("items", [])
	return jsonify({"calendars": calendar_options(calendars), "pair": {"timed_calendar_id": pair.timed_calendar_id, "allday_calendar_id": pair.allday_calendar_id} if pair else None})


@bp.post("/api/setup")
@login_required
def api_save_setup():
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	data = request.get_json(silent=True) or request.form
	timed_calendar_id = data.get("timed_calendar_id")
	allday_calendar_id = data.get("allday_calendar_id")
	if not timed_calendar_id or not allday_calendar_id:
		return jsonify({"error": "missing_calendar_id", "message": "Choose both calendars."}), 400
	if timed_calendar_id == allday_calendar_id:
		return jsonify({"error": "same_calendar", "message": "Choose two different calendars."}), 400
	pair = editable_pair_for_setup(user, timed_calendar_id, allday_calendar_id)
	if pair.timed_calendar_id != timed_calendar_id:
		pair.timed_sync_token = None
	if pair.allday_calendar_id != allday_calendar_id:
		pair.allday_sync_token = None
	pair.timed_calendar_id = timed_calendar_id
	pair.allday_calendar_id = allday_calendar_id
	db_session.commit()
	return jsonify({"status": "ok"})


@bp.get("/setup")
def setup_page():
	return serve_frontend()


@bp.post("/setup")
@login_required
def setup():
	user = current_user()
	if user is None:
		return redirect(frontend_url("/", message="Please sign in before choosing calendars."))
	timed_calendar_id = request.form["timed_calendar_id"]
	allday_calendar_id = request.form["allday_calendar_id"]
	if timed_calendar_id == allday_calendar_id:
		return redirect(frontend_url("/setup", message="Choose two different calendars."))
	pair = editable_pair_for_setup(user, timed_calendar_id, allday_calendar_id)
	if pair.timed_calendar_id != timed_calendar_id:
		pair.timed_sync_token = None
	if pair.allday_calendar_id != allday_calendar_id:
		pair.allday_sync_token = None
	pair.timed_calendar_id = timed_calendar_id
	pair.allday_calendar_id = allday_calendar_id
	db_session.commit()
	return redirect(frontend_url("/", message="Calendar pair saved."))


@bp.post("/api/sync")
@login_required
def api_sync_now():
	user = current_user()
	pair = active_pair(user)
	if not pair:
		return jsonify({"error": "setup_required", "message": "Select calendars before syncing."}), 400
	try:
		run = run_sync_for_pair(current_calendar_service(), pair)
		return jsonify({"status": "ok", "run": serialize_run(run)})
	except Exception as exc:
		return jsonify({"error": "sync_failed", "message": str(exc)}), 500


@bp.post("/api/deleted-events/clear")
@login_required
def api_clear_deleted_events():
	user = current_user()
	pair = active_pair(user)
	if not pair:
		return jsonify({"error": "setup_required", "message": "Select calendars before clearing deleted events."}), 400
	try:
		result = clear_deleted_event_mappings(current_calendar_service(), pair)
		return jsonify({"status": "ok", "result": result})
	except Exception as exc:
		return jsonify({"error": "clear_deleted_failed", "message": str(exc)}), 500


@bp.get("/sync-jobs")
def sync_jobs_page():
	return serve_frontend()


@bp.get("/api/sync-jobs")
@login_required
def api_sync_jobs():
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	jobs = SyncJob.query.filter_by(user_id=user.id).order_by(SyncJob.created_at.desc()).all()
	return jsonify({"jobs": [serialize_sync_job(job) for job in jobs]})


@bp.post("/api/sync-jobs")
@login_required
def api_create_sync_job():
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	data = request.get_json(silent=True) or {}
	friendly_name = (data.get("friendly_name") or "").strip()
	source_calendar_id = (data.get("source_calendar_id") or "").strip()
	target_calendar_id = (data.get("target_calendar_id") or "").strip()
	errors = {}
	if not friendly_name:
		errors["friendly_name"] = "Friendly name is required."
	if not source_calendar_id:
		errors["source_calendar_id"] = "Source calendar ID is required."
	if not target_calendar_id:
		errors["target_calendar_id"] = "Target calendar ID is required."
	if errors:
		return jsonify({"error": "validation_failed", "message": "Check the sync job fields.", "errors": errors}), 400
	pair = get_or_create_calendar_pair(user.id, source_calendar_id, target_calendar_id)
	job = SyncJob(user_id=user.id, calendar_pair_id=pair.id, friendly_name=friendly_name, source_calendar_id=source_calendar_id, target_calendar_id=target_calendar_id, enabled=True)
	db_session.add(job)
	db_session.commit()
	return jsonify({"job": serialize_sync_job(job)}), 201


@bp.patch("/api/sync-jobs/<int:job_id>")
@login_required
def api_update_sync_job(job_id):
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	job = SyncJob.query.filter_by(id=job_id, user_id=user.id).one_or_none()
	if not job:
		return jsonify({"error": "not_found", "message": "Sync job not found."}), 404
	data = request.get_json(silent=True) or {}
	if "enabled" not in data or not isinstance(data.get("enabled"), bool):
		return jsonify({"error": "validation_failed", "message": "Enabled must be true or false."}), 400
	job.enabled = data["enabled"]
	db_session.commit()
	return jsonify({"job": serialize_sync_job(job)})


@bp.delete("/api/sync-jobs/<int:job_id>")
@login_required
def api_delete_sync_job(job_id):
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	job = SyncJob.query.filter_by(id=job_id, user_id=user.id).one_or_none()
	if not job:
		return jsonify({"error": "not_found", "message": "Sync job not found."}), 404
	db_session.delete(job)
	db_session.commit()
	return jsonify({"status": "ok"})


@bp.post("/api/sync-jobs/run-all")
@login_required
def api_run_sync_jobs():
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	result = run_all_enabled_sync_jobs(user.id)
	return jsonify({"ok": True, "result": result})


@bp.get("/api/cron/sync")
def api_cron_sync():
	expected = os.environ.get("CRON_SECRET")
	auth_header = request.headers.get("Authorization")
	if not expected or auth_header != f"Bearer {expected}":
		return no_store_json({"ok": False, "error": "Unauthorized"}, 401)
	result = run_all_enabled_sync_jobs()
	return no_store_json({"ok": True, "result": result})


@bp.post("/sync")
@login_required
def sync_now():
	user = current_user()
	pair = active_pair(user)
	if not pair:
		return redirect(frontend_url("/setup", message="Select calendars before syncing."))
	try:
		run = run_sync_for_pair(current_calendar_service(), pair)
		message = f"Sync complete: {run.message}"
	except Exception as exc:
		message = f"Sync failed: {exc}"
	return redirect(frontend_url("/", message=message))


@bp.route("/sync/cron", methods=["GET", "POST"])
def sync_cron():
	return api_cron_sync()


@bp.get("/sync-runs")
def sync_runs():
	return serve_frontend()


@bp.get("/api/sync-runs")
@login_required
def api_sync_runs():
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	pair = active_pair(user)
	runs = SyncRun.query.filter_by(calendar_pair_id=pair.id).order_by(SyncRun.started_at.desc()).limit(50).all() if pair else []
	return jsonify({"runs": [serialize_run(run) for run in runs]})


@bp.get("/conflicts")
def conflicts():
	return serve_frontend()


@bp.get("/api/conflicts")
@login_required
def api_conflicts():
	user = current_user()
	if user is None:
		return jsonify({"error": "auth_required"}), 401
	pair = active_pair(user)
	items = Conflict.query.filter_by(calendar_pair_id=pair.id).order_by(Conflict.created_at.desc()).limit(50).all() if pair else []
	return jsonify({"conflicts": [serialize_conflict(item) for item in items]})


@bp.get("/assets/<path:path>")
def frontend_assets(path):
	if FRONTEND_DIST.exists():
		return send_from_directory(FRONTEND_DIST / "assets", path)
	abort(404)


@bp.get("/<path:path>")
def spa_fallback(path):
	if path.startswith("api/") or path in {"auth/start", "auth/callback", "logout", "health", "sync/cron"}:
		abort(404)
	return serve_frontend()
