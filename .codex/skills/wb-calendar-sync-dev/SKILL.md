---
name: wb-calendar-sync-dev
description: Work on the WB Calendar Sync repository, a Windows-developed Flask, SQLAlchemy, Google Calendar API, and Vite/React/TypeScript app deployed to Vercel. Use when changing local dev workflow, OAuth redirects, sync behavior, React UI, VS Code tasks, Vercel deployment config, env files, or verification steps for this repo.
---

# WB Calendar Sync Dev

## Overview

Use this repo-specific workflow to avoid rediscovering the Flask/Vite/OAuth/Vercel wiring. Keep local Python work inside `.venv`, keep frontend development on Vite port 5173, and keep Flask focused on API/OAuth/cron endpoints during development.

## Local Development

- Run Flask from the virtual environment:

```powershell
.\.venv\Scripts\flask.exe --app app run --host 127.0.0.1 --port 5000 --debug
```

- Run Vite separately:

```powershell
npm run dev -- --port 5173 --strictPort
```

- Open `http://localhost:5173/`; do not use Flask port 5000 as the main browser app during frontend development.
- Keep `FRONTEND_BASE_URL=http://localhost:5173` in `.env.local` so OAuth and form redirects return to Vite.
- Keep `GOOGLE_REDIRECT_URI=http://localhost:5000/auth/callback`; Google calls Flask, then Flask redirects back to Vite.
- When backend behavior looks stale, inspect and kill old Flask processes before retesting:

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
Get-Process | Where-Object { $_.ProcessName -like '*flask*' -or $_.Path -like '*flask*' }
```

Then restart Flask from `.venv` and verify `http://127.0.0.1:5000/health`.

## Verification

Use these checks after meaningful changes:

```powershell
npm run typecheck
npm run build
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall app api migrations tests
```

For browser checks, verify Vite serves `index.html` with `/@vite/client` and `/src/main.tsx`, and that `http://127.0.0.1:5173/api/app-state` proxies to Flask.

## Coding Style

- Name ordinary functions with verbs or verb phrases, not nouns. Use names like `callAPI()` rather than `api()`.
- Components and other framework-special functions may keep noun-style names when that is idiomatic.

## Sync Behavior Notes

- Manual sync, sync jobs, and cron should use the same bidirectional sync engine for the selected hourly/all-day calendar pair.
- Hourly events mirror to all-day titles with a time prefix, e.g. `Appointment` at 9am becomes `9am Appointment`; all-day `9am Appointment2` mirrors back to hourly `Appointment2`.
- Summary, description, location, and Google Meet/conference data are the core synchronized fields. Do not write visible provenance such as `Synced from...` or `Original event ID...` into descriptions.
- Google Calendar writes that include `conferenceData` must pass `conferenceDataVersion=1` on insert/update calls.
- Hidden Google `extendedProperties.private` are still required for idempotency and recovery when local mappings are missing.
- When both mapped events were edited before the next sync, the earlier-created Google event wins and a conflict is recorded for debugging.
- The “Clear deleted events” action removes only local `event_mappings` where both mapped Google events are already deleted/cancelled. Do not use it to delete Google events.
- Unmapped deleted/cancelled Google tombstones should be counted as `ignored_deleted` internally and omitted from user-facing sync summaries.
- Tests for sync helpers may use fake Google services and SQLAlchemy rows. Use unique pair IDs and explicit cleanup around committed helper behavior.

## Memory Hygiene

- Persist notable learnings from repo work before finishing: user-facing behavior and operations in `README.md`, general agent instructions in `AGENTS.md`, and Codex-specific workflow guidance in this skill file.

## Vercel Notes

- Vercel runs `npm run build`; Flask serves `frontend/dist` in production.
- Do not set `FRONTEND_BASE_URL` in production unless redirects must intentionally leave the current host.
- Production `DATABASE_URL` must be Postgres, not SQLite.
- Use psycopg v3, not `psycopg2-binary`; newer Vercel Python runtimes may fail building psycopg2. The app rewrites standard `postgresql://...` and `postgres://...` URLs to `postgresql+psycopg://...`.
- Add the production Google redirect URI in Google Cloud: `https://YOUR-VERCEL-APP.vercel.app/auth/callback`.
- Run Alembic migrations against the production database before relying on the deployed app.
- Set `CRON_SECRET` to a random 16+ character value in Vercel Project Settings for Production and Preview as needed. Vercel Cron calls `/api/cron/sync` with `Authorization: Bearer <CRON_SECRET>`.
