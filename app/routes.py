import os
from functools import wraps
from datetime import timezone
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlencode

from flask import Blueprint, abort, jsonify, redirect, request, send_from_directory, session

from app.db import db_session
from app.config import GOOGLE_SCOPES
from app.google_client import convert_expiry_for_database, current_calendar_service, current_user, credentials_from_token, make_flow, missing_required_scopes, userinfo_service
from app.models import CalendarPair, Conflict, OAuthToken, SyncRun, User
from app.sync import run_sync_for_pair


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
	pair = active_pair(user)
	if pair is None:
		pair = CalendarPair()
		pair.user_id = user.id
		db_session.add(pair)
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
	pair = active_pair(user)
	timed_calendar_id = request.form["timed_calendar_id"]
	allday_calendar_id = request.form["allday_calendar_id"]
	if timed_calendar_id == allday_calendar_id:
		return redirect(frontend_url("/setup", message="Choose two different calendars."))
	if pair is None:
		pair = CalendarPair()
		pair.user_id = user.id
		db_session.add(pair)
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
	import os
	expected = os.environ.get("CRON_SECRET")
	header = request.headers.get("Authorization", "")
	form_secret = request.form.get("secret") or request.args.get("secret")
	if expected and header != f"Bearer {expected}" and form_secret != expected:
		abort(401)
	pairs = CalendarPair.query.all()
	results = []
	for pair in pairs:
		token = OAuthToken.query.filter_by(user_id=pair.user_id).one_or_none()
		if not token:
			continue
		from googleapiclient.discovery import build
		service = build("calendar", "v3", credentials=credentials_from_token(token), cache_discovery=False)
		try:
			run = run_sync_for_pair(service, pair)
			results.append({"pair_id": pair.id, "status": run.status, "message": run.message})
		except Exception as exc:
			results.append({"pair_id": pair.id, "status": "error", "message": str(exc)})
	return jsonify({"status": "ok", "results": results})


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
