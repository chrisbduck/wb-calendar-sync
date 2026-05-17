# Calendar Sync

A small Flask app that mirrors timed Google Calendar events into all-day Google Calendar events. The first implementation is intentionally one-way: timed calendar to all-day calendar.

## Local Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env.local
```

Fill in Google OAuth values in `.env.local`, then initialize the database:

```powershell
alembic upgrade head
flask --app app run --debug
```

Local SQLite is supported with:

```env
DATABASE_URL=sqlite:///local.db
```

Production should use Postgres, such as Neon:

```env
DATABASE_URL=postgresql://...
```

The app loads `.env` first and `.env.local` second, with `.env.local` taking precedence. Keep local secrets in `.env.local`; both `.env` and `.env.local` are ignored by git.

## Google OAuth

Create a Google Cloud project, enable Google Calendar API, configure the OAuth consent screen in testing mode, and create a web OAuth client. Add these redirect URIs:

```text
http://localhost:5000/auth/callback
https://YOUR-VERCEL-APP.vercel.app/auth/callback
```

In the OAuth consent screen's Data Access section, add:

```text
https://www.googleapis.com/auth/calendar
```

If the callback reports that Google did not grant Calendar access, confirm that the Google Calendar API is enabled, that this scope is listed on the consent screen, and that your Google account is included as a test user. Restart sign-in from `/`; do not refresh an old `/auth/callback?...` URL because OAuth codes are single-use.

## Vercel

The Vercel entrypoint is `api/index.py`. The app exposes `/sync/cron`, protected by `CRON_SECRET`, and `vercel.json` schedules it every 15 minutes.

## Sync Behavior

The sync engine stores mappings in `event_mappings` and also writes Google Calendar private extended properties on mirrored events:

```json
{
  "calendarSyncApp": "true",
  "sourceEventId": "...",
  "sourceCalendarId": "..."
}
```

This makes repeated syncs idempotent and helps recover if the database mapping is missing but the mirrored event already exists.

## Local Troubleshooting

Always install and run from the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\flask.exe --app app run --debug
```

On Windows, Python's timezone support may need the `tzdata` package; it is included in `requirements.txt`.

Some environments set `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY` to a dead local proxy such as `http://127.0.0.1:9`. The app's Google OAuth token exchange and token refresh intentionally ignore ambient proxy environment variables so local sign-in is not routed through that proxy.

If Chrome shows `net::ERR_BLOCKED_BY_CLIENT` while verifying localhost, use a clean Chrome profile with extensions disabled instead of a normal browsing profile. Example:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\codex-chrome-profile" --disable-extensions --disable-component-extensions-with-background-pages --disable-default-apps --no-first-run --no-default-browser-check http://127.0.0.1:5000/
```
