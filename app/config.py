import os

from dotenv import load_dotenv


load_dotenv(".env")
load_dotenv(".env.local", override=True)


GOOGLE_SCOPES = [
	"https://www.googleapis.com/auth/calendar",
	"https://www.googleapis.com/auth/userinfo.email",
	"https://www.googleapis.com/auth/userinfo.profile",
	"openid",
]


def allowed_google_emails():
	value = os.environ.get("ALLOWED_GOOGLE_EMAILS", "")
	return {email.strip().lower() for email in value.replace(";", ",").split(",") if email.strip()}


def is_google_email_allowed(email):
	allowed = allowed_google_emails()
	return bool(allowed and email and email.strip().lower() in allowed)


def database_url():
	url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
	if os.environ.get("VERCEL") and url.startswith("sqlite"):
		raise RuntimeError("SQLite is only for local development. Set DATABASE_URL to a Postgres connection string before deploying to Vercel.")
	if url.startswith("postgres://"):
		return url.replace("postgres://", "postgresql+psycopg://", 1)
	if url.startswith("postgresql://"):
		return url.replace("postgresql://", "postgresql+psycopg://", 1)
	return url


def google_redirect_uri():
	return os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/auth/callback")
