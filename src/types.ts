export type User = { id: number; email: string };
export type Pair = { id: number; timed_calendar_id: string; allday_calendar_id: string; backup_calendar_id: string | null; timed_calendar_name: string; allday_calendar_name: string; backup_calendar_name: string | null };
export type SyncRun = { id: number; started_at: string | null; finished_at: string | null; status: string; message: string | null };
export type CalendarOption = { id: string; name: string; label: string };
export type Conflict = { id: number; created_at: string | null; resolved_at: string | null; timed_event_id: string | null; allday_event_id: string | null; reason: string };
export type SyncJob = { id: number; friendly_name: string; source_calendar_id: string; target_calendar_id: string; backup_calendar_id: string | null; enabled: boolean; created_at: string | null; updated_at: string | null; last_run_at: string | null; last_status: string | null; last_error: string | null };
export type AppState = { user: User | null; pair: Pair | null; recent_runs: SyncRun[]; last_synced_at: string | null };
export type RouteName = "home" | "setup" | "logs" | "conflicts" | "syncJobs";
