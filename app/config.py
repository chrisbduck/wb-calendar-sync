import os


GOOGLE_SCOPES = [
	"https://www.googleapis.com/auth/calendar",
	"https://www.googleapis.com/auth/userinfo.email",
	"https://www.googleapis.com/auth/userinfo.profile",
	"openid",
]


def database_url():
	url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
	if url.startswith("postgres://"):
		return url.replace("postgres://", "postgresql://", 1)
	return url


def google_redirect_uri():
	return os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:5000/auth/callback")
