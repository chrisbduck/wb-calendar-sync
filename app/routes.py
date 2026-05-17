from functools import wraps

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

from app.db import db_session
from app.google_client import current_calendar_service, current_user, credentials_from_token, make_flow, missing_required_scopes, userinfo_service
from app.models import CalendarPair, Conflict, OAuthToken, SyncRun, User
from app.sync import run_sync_for_pair


bp = Blueprint("main", __name__)


def login_required(view):
	@wraps(view)
	def wrapped(*args, **kwargs):
		if not current_user():
			flash("Please sign in with Google first.")
			return redirect(url_for("main.index"))
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


@bp.get("/")
def index():
	user = current_user()
	pair = active_pair(user) if user else None
	service = current_calendar_service() if user and pair else None
	calendar_names = selected_calendar_names(service, pair)
	runs = SyncRun.query.filter_by(calendar_pair_id=pair.id).order_by(SyncRun.started_at.desc()).limit(5).all() if pair else []
	return render_template("index.html", user=user, pair=pair, calendar_names=calendar_names, runs=runs)


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
		flash("Google sign-in worked, but Google did not grant Calendar access. In Google Cloud Console, make sure the Google Calendar API is enabled and add this scope to the OAuth consent screen/Data Access: https://www.googleapis.com/auth/calendar. Then start sign-in again.")
		return redirect(url_for("main.index"))
	profile = userinfo_service(credentials).userinfo().get().execute()
	user = User.query.filter_by(google_sub=profile["id"]).one_or_none()
	if not user:
		user = User(email=profile["email"], google_sub=profile["id"])
		db_session.add(user)
		db_session.flush()
	token = OAuthToken.query.filter_by(user_id=user.id).one_or_none()
	if not token:
		token = OAuthToken(user_id=user.id)
		db_session.add(token)
	token.access_token = credentials.token
	token.refresh_token = credentials.refresh_token or token.refresh_token
	token.token_uri = credentials.token_uri
	token.client_id = credentials.client_id
	token.client_secret = credentials.client_secret
	token.scopes = " ".join(credentials.scopes or [])
	token.expiry = credentials.expiry
	db_session.commit()
	session["user_id"] = user.id
	flash("Signed in with Google.")
	return redirect(url_for("main.setup"))


@bp.get("/logout")
def logout():
	session.clear()
	flash("Signed out.")
	return redirect(url_for("main.index"))


@bp.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
	user = current_user()
	service = current_calendar_service()
	if request.method == "POST":
		timed_calendar_id = request.form["timed_calendar_id"]
		allday_calendar_id = request.form["allday_calendar_id"]
		if timed_calendar_id == allday_calendar_id:
			flash("Choose two different calendars.")
			return redirect(url_for("main.setup"))
		pair = active_pair(user)
		if not pair:
			pair = CalendarPair(user_id=user.id)
			db_session.add(pair)
		if pair.timed_calendar_id != timed_calendar_id:
			pair.timed_sync_token = None
		pair.timed_calendar_id = timed_calendar_id
		pair.allday_calendar_id = allday_calendar_id
		db_session.commit()
		flash("Calendar pair saved.")
		return redirect(url_for("main.index"))
	calendars = service.calendarList().list().execute().get("items", [])
	pair = active_pair(user)
	return render_template("setup.html", user=user, calendar_options=calendar_options(calendars), pair=pair)


@bp.post("/sync")
@login_required
def sync_now():
	user = current_user()
	pair = active_pair(user)
	if not pair:
		flash("Select calendars before syncing.")
		return redirect(url_for("main.setup"))
	try:
		run = run_sync_for_pair(current_calendar_service(), pair)
		flash(f"Sync complete: {run.message}")
	except Exception as exc:
		flash(f"Sync failed: {exc}")
	return redirect(url_for("main.index"))


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
@login_required
def sync_runs():
	user = current_user()
	pair = active_pair(user)
	runs = SyncRun.query.filter_by(calendar_pair_id=pair.id).order_by(SyncRun.started_at.desc()).limit(50).all() if pair else []
	return render_template("sync_runs.html", user=user, pair=pair, runs=runs)


@bp.get("/conflicts")
@login_required
def conflicts():
	user = current_user()
	pair = active_pair(user)
	items = Conflict.query.filter_by(calendar_pair_id=pair.id).order_by(Conflict.created_at.desc()).limit(50).all() if pair else []
	return render_template("conflicts.html", user=user, pair=pair, conflicts=items)
