import os
from datetime import timezone

from flask import session
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from requests import Session

from app.config import GOOGLE_SCOPES, google_redirect_uri
from app.db import db_session
from app.models import OAuthToken, User


def missing_required_scopes(granted_scopes):
	granted = set(granted_scopes or [])
	return sorted(set(GOOGLE_SCOPES) - granted)


def no_proxy_session():
	session = Session()
	session.trust_env = False
	return session


def convert_expiry_for_google(expiry):
	if not expiry:
		return None
	if expiry.tzinfo is None:
		return expiry
	return expiry.astimezone(timezone.utc).replace(tzinfo=None)


def convert_expiry_for_database(expiry):
	if not expiry:
		return None
	if expiry.tzinfo is None:
		return expiry.replace(tzinfo=timezone.utc)
	return expiry.astimezone(timezone.utc)


def client_config():
	return {
		"web": {
			"client_id": os.environ["GOOGLE_CLIENT_ID"],
			"client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
			"auth_uri": "https://accounts.google.com/o/oauth2/auth",
			"token_uri": "https://oauth2.googleapis.com/token",
			"redirect_uris": [google_redirect_uri()],
		}
	}


def make_flow(state=None):
	if google_redirect_uri().startswith("http://localhost"):
		os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
	os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
	flow = Flow.from_client_config(client_config(), scopes=GOOGLE_SCOPES, state=state)
	flow.oauth2session.trust_env = False
	flow.redirect_uri = google_redirect_uri()
	return flow


def credentials_from_token(token: OAuthToken):
	credentials = Credentials(token=token.access_token, refresh_token=token.refresh_token, token_uri=token.token_uri, client_id=token.client_id, client_secret=token.client_secret, scopes=token.scopes.split(" "))
	credentials.expiry = convert_expiry_for_google(token.expiry)
	if credentials.expired and credentials.refresh_token:
		credentials.refresh(Request(session=no_proxy_session()))
		token.access_token = credentials.token
		token.expiry = convert_expiry_for_database(credentials.expiry)
		db_session.commit()
	return credentials


def current_user():
	user_id = session.get("user_id")
	return db_session.get(User, user_id) if user_id else None


def current_calendar_service():
	user = current_user()
	if not user or not user.tokens:
		return None
	return build("calendar", "v3", credentials=credentials_from_token(user.tokens[0]), cache_discovery=False)


def userinfo_service(credentials):
	return build("oauth2", "v2", credentials=credentials, cache_discovery=False)
