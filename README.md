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
```

Run the backend and frontend in separate terminals during development:

```powershell
.\.venv\Scripts\flask.exe --app app run --host 127.0.0.1 --port 5000 --debug
```

```powershell
npm run dev
```

Open the app at:

```text
http://localhost:5173/
```

Vite serves the React app directly and live-reloads frontend changes. Flask stays on port 5000 for API, OAuth, and cron endpoints.

In VS Code, you can also run the default task:

```text
Terminal: Run Build Task -> Start dev servers
```

That starts both the Flask backend and Vite frontend.

Local SQLite is supported with:

```env
DATABASE_URL=sqlite:///local.db
```

Production should use Postgres, such as Neon:

```env
DATABASE_URL=postgresql://...
```

The app uses psycopg v3 for Postgres. Standard Neon/Vercel `postgresql://...` or `postgres://...` URLs are normalized internally to SQLAlchemy's `postgresql+psycopg://...` driver URL.

The app loads `.env` first and `.env.local` second, with `.env.local` taking precedence. Keep local secrets in `.env.local`; both `.env` and `.env.local` are ignored by git.

For local Vite development, set:

```env
FRONTEND_BASE_URL=http://localhost:5173
```

That makes OAuth and form redirects return to the Vite dev server after Flask handles backend work. Do not change the Google redirect URI to port 5173; Google should still call Flask at `http://localhost:5000/auth/callback`.

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

The Vercel entrypoint is `api/index.py`. Vercel runs `npm run build` first so Flask can serve the Vite/React output from `frontend/dist`. The app exposes `/sync/cron`, protected by `CRON_SECRET`, and `vercel.json` schedules it daily.

Before deployment:

- Set `DATABASE_URL` to Neon/Postgres. The app intentionally refuses to use SQLite when running on Vercel.
- Set `FLASK_SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and `CRON_SECRET` in Vercel.
- Use `GOOGLE_REDIRECT_URI=https://YOUR-VERCEL-APP.vercel.app/auth/callback` in production.
- Do not set `FRONTEND_BASE_URL` in production unless you explicitly want redirects to leave the current deployed host.
- Run Alembic migrations against the production database before using the deployed app.

## Frontend

The UI is a Vite/React app in `src/`. During frontend development, run:

```powershell
npm run dev
```

Vite serves the browser app from port 5173 and proxies `/api`, `/auth`, `/logout`, and `/health` to Flask on port 5000. This is the native local development workflow; frontend edits should hot-reload without restarting Flask.

For production, `npm run build` writes the static app to `frontend/dist`, which Flask serves for `/`, `/setup`, `/sync-runs`, and `/conflicts`.

The React app is split so the root data fetch lives in `src/App.tsx`, while page text and layout live under `src/pages/`. Small copy/layout edits on the home page should usually touch `src/pages/HomePage.tsx`, letting Vite hot-reload that page module without restarting Flask.

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
.\.venv\Scripts\flask.exe --app app run --host 127.0.0.1 --port 5000 --debug
```

On Windows, Python's timezone support may need the `tzdata` package; it is included in `requirements.txt`.

Some environments set `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY` to a dead local proxy such as `http://127.0.0.1:9`. The app's Google OAuth token exchange and token refresh intentionally ignore ambient proxy environment variables so local sign-in is not routed through that proxy.

If Chrome shows `net::ERR_BLOCKED_BY_CLIENT` while verifying localhost, use a clean Chrome profile with extensions disabled instead of a normal browsing profile. Example:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:TEMP\codex-chrome-profile" --disable-extensions --disable-component-extensions-with-background-pages --disable-default-apps --no-first-run --no-default-browser-check http://127.0.0.1:5000/
```

## Codex Skill

Repo-specific Codex guidance lives in `.codex/skills/wb-calendar-sync-dev/SKILL.md`. Use it when working on local dev workflow, OAuth redirects, React UI structure, verification, or Vercel deployment for this app.
