# Calendar Sync

A small Flask app that syncs between an hourly Google Calendar and an all-day Google Calendar. The primary direction mirrors hourly events into all-day events, and the reverse direction can turn all-day event titles with clear times into hourly events.

## Local Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
npm install
Copy-Item .env.example .env.local
```

Fill in Google OAuth values in `.env.local`, then initialize the database:

```powershell
alembic upgrade head
npm run build
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

The Vercel entrypoint is `api/index.py`. Vercel runs `npm run build` first so Flask can serve the Vite/React output from `frontend/dist`. The app exposes `/sync/cron`, protected by `CRON_SECRET`, and `vercel.json` schedules it every 15 minutes.

## Frontend

The UI is a Vite/React app in `src/`. It builds into `frontend/dist`, which Flask serves for `/`, `/setup`, `/sync-runs`, and `/conflicts`. During frontend development, run:

```powershell
npm run dev
```

Vite proxies `/api`, `/auth`, `/logout`, and `/health` to the Flask server on port 5000.

## Sync Behavior

The sync engine stores mappings in `event_mappings` and also writes Google Calendar private extended properties on mirrored events:

```json
{
  "calendarSyncApp": "true",
  "syncDirection": "timed_to_allday",
  "sourceEventId": "...",
  "sourceCalendarId": "..."
}
```

This makes repeated syncs idempotent, helps recover if the database mapping is missing but the mirrored event already exists, and prevents app-created mirror events from being synced back as if they were new source events.

Timed-to-all-day sync creates one all-day event whose title starts with the source start time, such as `2pm Doctor`.

All-day-to-hourly sync reads true source events from the all-day calendar only. If the title has a clear time such as `5am`, `6pm`, or `5:30am`, it creates an hourly event at that time. The default duration is one hour. Clear ranges such as `5pm to 7pm` or `5-7pm` set the duration from the range, with an omitted start meridiem inferred from the end where clear. If no clear time is present, the event is mirrored as an all-day event on the hourly calendar.

For performance and simplicity, sync only acts on events that start one week ago or later, regardless of when they end. Full sync queries ask Google for that same one-week-back window, and incremental sync processing skips returned events that started before that cutoff.

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
