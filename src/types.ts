export type User = { id: number; email: string };
export type Pair = { id: number; timed_calendar_id: string; allday_calendar_id: string; timed_calendar_name: string; allday_calendar_name: string };
export type SyncRun = { id: number; started_at: string | null; finished_at: string | null; status: string; message: string | null };
export type CalendarOption = { id: string; name: string; label: string };
export type Conflict = { id: number; created_at: string | null; resolved_at: string | null; timed_event_id: string | null; allday_event_id: string | null; reason: string };
export type AppState = { user: User | null; pair: Pair | null; recent_runs: SyncRun[]; last_synced_at: string | null };
export type RouteName = "home" | "setup" | "logs" | "conflicts";
