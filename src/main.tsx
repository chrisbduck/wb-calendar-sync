import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { CalendarDays, CheckCircle2, Home, Loader2, LogOut, RefreshCcw, Settings, TriangleAlert } from "lucide-react";
import "./styles.css";

type User = { id: number; email: string };
type Pair = { id: number; timed_calendar_id: string; allday_calendar_id: string; timed_calendar_name: string; allday_calendar_name: string };
type SyncRun = { id: number; started_at: string | null; finished_at: string | null; status: string; message: string | null };
type CalendarOption = { id: string; name: string; label: string };
type Conflict = { id: number; created_at: string | null; resolved_at: string | null; timed_event_id: string | null; allday_event_id: string | null; reason: string };
type AppState = { user: User | null; pair: Pair | null; recent_runs: SyncRun[]; last_synced_at: string | null };

const api = async <T,>(url: string, options?: RequestInit): Promise<T> => {
	const response = await fetch(url, { credentials: "same-origin", headers: { "Content-Type": "application/json", ...(options?.headers || {}) }, ...options });
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(data.message || data.error || `Request failed: ${response.status}`);
	}
	return data as T;
};

const localDateTime = (value: string | null) => {
	if (!value) return "Not yet";
	return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
};

const routeFromPath = () => {
	const path = window.location.pathname;
	if (path.startsWith("/setup")) return "setup";
	if (path.startsWith("/sync-runs")) return "logs";
	if (path.startsWith("/conflicts")) return "conflicts";
	return "home";
};

function App() {
	const [route, setRoute] = useState(routeFromPath());
	const [state, setState] = useState<AppState | null>(null);
	const [loading, setLoading] = useState(true);
	const [message, setMessage] = useState(new URLSearchParams(window.location.search).get("message") || "");

	const refreshState = async () => {
		const next = await api<AppState>("/api/app-state");
		setState(next);
	};

	useEffect(() => {
		refreshState().catch((error) => setMessage(error.message)).finally(() => setLoading(false));
	}, []);

	const navigate = (nextRoute: string, path: string) => {
		window.history.pushState({}, "", path);
		setRoute(nextRoute);
		setMessage("");
	};

	useEffect(() => {
		const onPopState = () => setRoute(routeFromPath());
		window.addEventListener("popstate", onPopState);
		return () => window.removeEventListener("popstate", onPopState);
	}, []);

	const content = useMemo(() => {
		if (loading) return <LoadingPanel />;
		if (route === "setup") return <SetupPage state={state} onMessage={setMessage} onSaved={() => { setMessage("Calendar pair saved."); refreshState(); navigate("home", "/"); }} />;
		if (route === "logs") return <RunsPage />;
		if (route === "conflicts") return <ConflictsPage />;
		return <HomePage state={state} onMessage={setMessage} onSynced={refreshState} onNavigate={navigate} />;
	}, [loading, route, state]);

	return (
		<div className="app-shell">
			<header className="topbar">
				<div className="brand"><CalendarDays size={22} /> WB Calendar Sync</div>
				<nav>
					<button className="nav-button" onClick={() => navigate("home", "/")}><Home size={16} /> Home</button>
					<button className="nav-button" onClick={() => navigate("setup", "/setup")}><Settings size={16} /> Setup</button>
					<button className="nav-button" onClick={() => navigate("logs", "/sync-runs")}><RefreshCcw size={16} /> Logs</button>
					<button className="nav-button" onClick={() => navigate("conflicts", "/conflicts")}><TriangleAlert size={16} /> Conflicts</button>
					{state?.user ? <a className="nav-link" href="/logout"><LogOut size={16} /> Sign out</a> : null}
				</nav>
			</header>
			<main>
				{message ? <div className="notice"><CheckCircle2 size={18} /> {message}</div> : null}
				{content}
			</main>
		</div>
	);
}

function LoadingPanel() {
	return <section className="panel"><Loader2 className="spin" /> Loading WB Calendar Sync...</section>;
}

function HomePage({ state, onMessage, onSynced, onNavigate }: { state: AppState | null; onMessage: (message: string) => void; onSynced: () => Promise<void>; onNavigate: (route: string, path: string) => void }) {
	const [syncing, setSyncing] = useState(false);
	const syncNow = async () => {
		setSyncing(true);
		onMessage("Sync started...");
		try {
			const result = await api<{ run: SyncRun }>("/api/sync", { method: "POST", body: "{}" });
			onMessage(`Sync complete${result.run.message ? `: ${result.run.message}` : "."}`);
			await onSynced();
		} catch (error) {
			onMessage(error instanceof Error ? error.message : "Sync failed.");
		} finally {
			setSyncing(false);
		}
	};

	if (!state?.user) {
		return (
			<section className="hero-panel">
				<h1>Sync timed and all-day Google calendars.</h1>
				<p>Connect Google Calendar, choose your timed source and all-day companion calendar, then keep both views aligned.</p>
				<a className="primary-button" href="/auth/start">Sign in with Google</a>
			</section>
		);
	}

	return (
		<section className="panel dashboard">
			<div className="section-heading">
				<div>
					<p className="eyebrow">Signed in as {state.user.email}</p>
					<h1>Calendar sync dashboard</h1>
				</div>
				<button className="secondary-button" onClick={() => onNavigate("home", "/")}><Home size={16} /> Home</button>
			</div>
			{state.pair ? (
				<>
					<div className="calendar-grid">
						<div><span>Timed calendar</span><strong>{state.pair.timed_calendar_name}</strong></div>
						<div><span>All-day calendar</span><strong>{state.pair.allday_calendar_name}</strong></div>
					</div>
					<div className="sync-row">
						<button className="primary-button" disabled={syncing} onClick={syncNow}>{syncing ? <Loader2 className="spin" size={18} /> : <RefreshCcw size={18} />} {syncing ? "Syncing..." : "Sync now"}</button>
						<div className="last-sync"><span>Last synced</span><strong>{localDateTime(state.last_synced_at)}</strong></div>
					</div>
					<RecentRuns runs={state.recent_runs} />
				</>
			) : (
				<div className="empty-state">
					<p>No calendar pair selected yet.</p>
					<button className="primary-button" onClick={() => onNavigate("setup", "/setup")}>Choose calendars</button>
				</div>
			)}
		</section>
	);
}

function RecentRuns({ runs }: { runs: SyncRun[] }) {
	if (!runs.length) return null;
	return <div className="table-wrap"><h2>Recent syncs</h2><RunsTable runs={runs} /></div>;
}

function SetupPage({ state, onMessage, onSaved }: { state: AppState | null; onMessage: (message: string) => void; onSaved: () => void }) {
	const [calendars, setCalendars] = useState<CalendarOption[]>([]);
	const [timed, setTimed] = useState("");
	const [allDay, setAllDay] = useState("");
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		api<{ calendars: CalendarOption[]; pair: { timed_calendar_id: string; allday_calendar_id: string } | null }>("/api/calendars")
			.then((data) => {
				setCalendars(data.calendars);
				setTimed(data.pair?.timed_calendar_id || data.calendars[0]?.id || "");
				setAllDay(data.pair?.allday_calendar_id || data.calendars[1]?.id || data.calendars[0]?.id || "");
			})
			.catch((error) => onMessage(error.message));
	}, []);

	if (!state?.user) return <section className="panel"><p>Please sign in before choosing calendars.</p><a className="primary-button" href="/auth/start">Sign in with Google</a></section>;

	const save = async (event: React.FormEvent) => {
		event.preventDefault();
		setSaving(true);
		onMessage("Saving setup...");
		try {
			await api("/api/setup", { method: "POST", body: JSON.stringify({ timed_calendar_id: timed, allday_calendar_id: allDay }) });
			onSaved();
		} catch (error) {
			onMessage(error instanceof Error ? error.message : "Setup failed.");
		} finally {
			setSaving(false);
		}
	};

	return (
		<section className="panel form-panel">
			<h1>Choose calendars</h1>
			<form onSubmit={save}>
				<label>Timed calendar<select value={timed} onChange={(event) => setTimed(event.target.value)}>{calendars.map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.label}</option>)}</select></label>
				<label>All-day calendar<select value={allDay} onChange={(event) => setAllDay(event.target.value)}>{calendars.map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.label}</option>)}</select></label>
				<button className="primary-button" disabled={saving}>{saving ? "Saving..." : "Save setup"}</button>
			</form>
		</section>
	);
}

function RunsPage() {
	const [runs, setRuns] = useState<SyncRun[]>([]);
	useEffect(() => { api<{ runs: SyncRun[] }>("/api/sync-runs").then((data) => setRuns(data.runs)); }, []);
	return <section className="panel"><h1>Sync logs</h1><RunsTable runs={runs} /></section>;
}

function RunsTable({ runs }: { runs: SyncRun[] }) {
	return <table><thead><tr><th>Started</th><th>Finished</th><th>Status</th><th>Message</th></tr></thead><tbody>{runs.length ? runs.map((run) => <tr key={run.id}><td>{localDateTime(run.started_at)}</td><td>{localDateTime(run.finished_at)}</td><td><span className={`status ${run.status}`}>{run.status}</span></td><td>{run.message || ""}</td></tr>) : <tr><td colSpan={4}>No sync runs yet.</td></tr>}</tbody></table>;
}

function ConflictsPage() {
	const [conflicts, setConflicts] = useState<Conflict[]>([]);
	useEffect(() => { api<{ conflicts: Conflict[] }>("/api/conflicts").then((data) => setConflicts(data.conflicts)); }, []);
	return <section className="panel"><h1>Conflicts</h1><table><thead><tr><th>Created</th><th>Timed event</th><th>All-day event</th><th>Reason</th></tr></thead><tbody>{conflicts.length ? conflicts.map((conflict) => <tr key={conflict.id}><td>{localDateTime(conflict.created_at)}</td><td>{conflict.timed_event_id || ""}</td><td>{conflict.allday_event_id || ""}</td><td>{conflict.reason}</td></tr>) : <tr><td colSpan={4}>No conflicts.</td></tr>}</tbody></table></section>;
}

createRoot(document.getElementById("root")!).render(<App />);
