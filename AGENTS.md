# Codex Repo Notes

## Environment-specific guidance

- This project is developed on Windows, so avoid Linux/Mac-specific commands.
- Use the project virtual environment for Python commands. Prefer `.\.venv\Scripts\python.exe -m ...`, `.\.venv\Scripts\pip.exe`, `.\.venv\Scripts\flask.exe`, and `.\.venv\Scripts\alembic.exe`; do not install dependencies into the global Python environment.
- Local configuration and secrets are kept in `.env.local`, not `.env`. The app deliberately loads `.env` first and `.env.local` second with override enabled.
- Use the native development split: Flask runs on `127.0.0.1:5000`, Vite runs on `127.0.0.1:5173`, and the browser should open `http://localhost:5173/`.
- Keep `FRONTEND_BASE_URL=http://localhost:5173` in local `.env.local` so OAuth/form redirects return to Vite. Keep `GOOGLE_REDIRECT_URI=http://localhost:5000/auth/callback`; do not move the Google callback to Vite.
- VS Code has a default task named `Start dev servers` that starts both Flask and Vite.
- The repo-local Codex skill at `.codex/skills/wb-calendar-sync-dev/SKILL.md` captures the current Flask/Vite/OAuth/Vercel workflow.
- After backend code changes, stale local Flask processes can keep serving old code. Check port `5000` and any `flask.exe` processes, kill stale ones, then restart `.\.venv\Scripts\flask.exe --app app run --host 127.0.0.1 --port 5000` and verify `/health`.
- `rg` may not be runnable in this workspace on Windows/Codex Desktop. In this repo it failed with an "Access is denied" launch error from the packaged `rg.exe`, so prefer PowerShell-native search commands first:
  - File search: `Get-ChildItem -Recurse -File`
  - Text search: `Get-ChildItem ... | Select-String -Pattern ...`
- When searching, avoid scanning `node_modules` or `frontend/dist` unless you explicitly need generated output.
- When enumerating files, avoid `.venv`, `.tmp`, `__pycache__`, and `local.db`; the workspace can contain permission-restricted temp directories after venv/pip work.
- If `python -m venv .venv` fails during `ensurepip` with temp-directory permissions, set `TEMP` and `TMP` to a workspace-local `.tmp` directory and rerun `.\.venv\Scripts\python.exe -m ensurepip --upgrade --default-pip`.
- This environment may define `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY` as `http://127.0.0.1:9`. Do not assume Google API failures are credential problems before checking proxy behavior; the app uses proxy-free requests sessions for OAuth token exchange and refresh.
- For external browser verification, the user's normal Chrome profile may block localhost with `net::ERR_BLOCKED_BY_CLIENT`. Launch a clean Chrome profile with extensions disabled and remote debugging instead of using the normal profile.
- Google OAuth must request and receive `https://www.googleapis.com/auth/calendar`. If Google returns only profile/email/openid scopes, check the Google Cloud consent screen Data Access scopes, enabled Calendar API, and test user list.
- Vercel production must use Postgres through `DATABASE_URL`; the app refuses SQLite when `VERCEL` is set. Run Alembic migrations against the production database before relying on the deployment.
- The app uses psycopg v3, not `psycopg2-binary`, because Vercel may build with newer CPython versions. Keep standard `postgresql://...` or `postgres://...` env values; `app.config.database_url()` rewrites them to `postgresql+psycopg://...` for SQLAlchemy.
- `CRON_SECRET` protects `/api/cron/sync`; Vercel sends it as `Authorization: Bearer <CRON_SECRET>`. Use a random 16+ character value and set it in Vercel Production and Preview if both should exercise cron behavior.
- Sync behavior is intentionally bidirectional for the selected hourly/all-day calendar pair. Do not add visible provenance text to event descriptions; hidden Google `extendedProperties.private` are the sync metadata.
- Sync core fields currently include summary, description, location, and Google Meet/conference data. Calendar API writes that include `conferenceData` must pass `conferenceDataVersion=1` on insert/update calls or Google may ignore the conference data.
- Attendees are intentionally not synced in either direction. Invited hourly events should still create all-day informational mirrors with the original description left as-is and Google Meet/conference data copied when Google allows it, but the mirror must not become a duplicate invitation thread.
- The “Clear deleted events” feature should only remove local `event_mappings` when both mapped Google events are already missing or cancelled. It must not delete live Google events or remove mappings when only one side is gone.
- Sync setup requires hourly, all-day, and backup calendars. Propagated deletes copy the live mapped event into the backup calendar before deleting it from its original calendar; if the live event changed since the last mapped etag, record a conflict and do not delete it.
- Deleted/cancelled Google tombstones without local mappings should be counted internally as `ignored_deleted`, not shown as user-facing `skipped`.
- Google can return special all-day events, especially birthdays without a birth year, with dates like `0000-06-01`. Python cannot parse year zero, so sync should skip those events and log their ID/title instead of failing the whole run with `year 0 is out of range`; remember the bad year-zero event may be a mapped counterpart loaded from the other calendar, not just the event currently being iterated.
- Sync run summaries should count actual calendar writes made by the sync, not merely source changes detected by Google. If both sides of a mapped pair are already deleted/cancelled, clear the mapping and keep the user-facing deleted count at zero.
- Sync jobs must run the calendar IDs stored on the job itself. A user's active setup pair can change later, so do not let a job follow a mutated `calendar_pair_id` relationship to a different hourly/all-day pair.
- Some sync helper tests intentionally touch SQLAlchemy state. Keep them isolated with unique `calendar_pair_id` values and explicit cleanup so failed local runs do not leave rows that affect later tests.
- When notable project behavior, deployment constraints, environment quirks, or user preferences are learned, persist them in the appropriate Markdown file before wrapping up: human-facing product/ops notes in `README.md`, general agent instructions in `AGENTS.md`, and repo-specific workflow details in `.codex/skills/wb-calendar-sync-dev/SKILL.md`.

## Coding style preferences

- Prefer keeping code on one line when it is still readable. Do not automatically split every parameter, prop, argument, or object field onto its own line.
- Optimize for keeping more code visible on screen rather than minimizing future merge conflicts from a single changed parameter.
- There is no strict maximum line length in this repo. Long lines are acceptable when they improve readability and avoid unnecessary vertical expansion.
- Treat roughly `150+` characters as acceptable when that keeps related code together. Word wrap is preferred over aggressive reformatting.
- Only expand calls, JSX props, or object literals across multiple lines when it materially improves readability.
- Name ordinary functions with verbs or verb phrases, not nouns. For example, prefer `callAPI()` over `api()`. Components and other framework-special functions may keep noun-style names when that is idiomatic.
- Indent with tabs, not spaces, with tab size = 4 spaces for all file types except JSON, which uses tab size = 2 spaces.
