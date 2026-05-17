# Calendar Sync

A small Flask app that mirrors timed Google Calendar events into all-day Google Calendar events. The first implementation is intentionally one-way: timed calendar to all-day calendar.

## Local Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in Google OAuth values in `.env`, then initialize the database:

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

## Google OAuth

Create a Google Cloud project, enable Google Calendar API, configure the OAuth consent screen in testing mode, and create a web OAuth client. Add these redirect URIs:

```text
http://localhost:5000/auth/callback
https://YOUR-VERCEL-APP.vercel.app/auth/callback
```

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
