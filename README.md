# Calendar Sync

A small Flask app that syncs between an hourly Google Calendar and an all-day Google Calendar. Hourly events are mirrored onto the all-day calendar with a time prefix, and all-day events with clear times are mirrored onto the hourly calendar.

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
CRON_SECRET=replace-with-a-random-16-character-or-longer-secret
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

The Vercel entrypoint is `api/index.py`. Vercel runs `npm run build` first so Flask can serve the Vite/React output from `frontend/dist`. The app exposes `/api/cron/sync`, protected by `CRON_SECRET`, and `vercel.json` schedules it daily at 10:00 UTC, which is 2am PST.

Before deployment:

- Set `DATABASE_URL` to Neon/Postgres. The app intentionally refuses to use SQLite when running on Vercel.
- Set `FLASK_SECRET_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and `CRON_SECRET` in Vercel.
- Use `GOOGLE_REDIRECT_URI=https://YOUR-VERCEL-APP.vercel.app/auth/callback` in production.
- Do not set `FRONTEND_BASE_URL` in production unless you explicitly want redirects to leave the current deployed host.
- Run Alembic migrations against the production database before using the deployed app.
- Confirm the cron appears in Vercel Dashboard -> Project -> Settings -> Cron Jobs after deployment, then check runtime logs for `/api/cron/sync`.

For local cron testing, an unauthenticated request should return `401`:

```powershell
Invoke-WebRequest http://localhost:5000/api/cron/sync -SkipHttpErrorCheck
```

With `CRON_SECRET` set in `.env.local`, an authorized request runs enabled jobs:

```powershell
Invoke-WebRequest http://localhost:5000/api/cron/sync -Headers @{ Authorization = "Bearer $env:CRON_SECRET" }
```

Vercel sends the cron secret as an `Authorization: Bearer <CRON_SECRET>` header. Use a random 16+ character value and set it for both Production and Preview environments if both deployments should be able to run the protected cron endpoint.

## Frontend

The UI is a Vite/React app in `src/`. During frontend development, run:

```powershell
npm run dev
```

Vite serves the browser app from port 5173 and proxies `/api`, `/auth`, `/logout`, and `/health` to Flask on port 5000. This is the native local development workflow; frontend edits should hot-reload without restarting Flask.

For production, `npm run build` writes the static app to `frontend/dist`, which Flask serves for `/`, `/setup`, `/sync-jobs`, `/sync-runs`, and `/conflicts`.

The React app is split so the root data fetch lives in `src/App.tsx`, while page text and layout live under `src/pages/`. Small copy/layout edits on the home page should usually touch `src/pages/HomePage.tsx`, letting Vite hot-reload that page module without restarting Flask.

The `/sync-jobs` admin page uses the currently selected hourly and all-day calendars from setup/home. Sync jobs are named with friendly names because Google calendar IDs are not human-readable. The “Run enabled jobs” action and Vercel cron both use the same backend sync runner.

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

This makes repeated syncs idempotent and helps recover if the database mapping is missing but the mirrored event already exists. These properties are hidden Google metadata; they are not added to the visible event description.

Timed-to-all-day sync creates one all-day event whose title starts with the source start time, such as `2pm Doctor`. For invited events authored in another time zone, the title uses the time in the selected hourly calendar's time zone.

All-day-to-hourly sync reads all-day event titles with clear times such as `5am`, `6pm`, or `5:30am`, and creates or updates an hourly event at that time. The default duration is one hour. Clear ranges such as `5pm to 7pm` or `5-7pm` set the duration from the range, with an omitted start meridiem inferred from the end where clear. If an all-day event spans multiple days and has a clear time, the hourly mirror repeats daily for each covered day. If an existing mapped all-day event is renamed without a clear time, the hourly event keeps its existing time and uses the all-day title as its summary.

After a pair is mapped, edits to summary, description, location, and Google Meet conferencing info can flow in either direction. Event descriptions sync exactly as entered; the app does not add connection fallback text or visible sync metadata to descriptions. If both sides are edited before sync runs, the earlier-created event wins and the app records a conflict for debugging.

Attendees are not synced in either direction. This is intentional because invitations from other people land on the hourly calendar, and copying those attendees to the all-day mirror could invite the sender or guests to a duplicate event. Mirrored events still sync Google Meet/conference data when Google allows it, but the mirror remains an informational copy rather than an invitation thread. Calendar writes request `sendUpdates=none` to avoid notification mail from sync-created changes.

Generated event descriptions should not include visible sync provenance such as `Synced from...` or `Original event ID...`. Hidden Google private extended properties are still used for idempotency and recovery.

The Home dashboard includes a “Clear deleted events” action. It removes local `event_mappings` only when both mapped Google events are already deleted or cancelled; it does not delete live Google events. Use it when sync summaries include confusing skipped/deleted historical events after both sides of a mapping are gone.

Sync run summaries count actual calendar writes made by the sync, not every source change Google reports. If a mapped hourly event and its all-day copy are already deleted/cancelled before sync runs, the app clears the local mapping and omits that pair from the user-facing deleted count.

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
