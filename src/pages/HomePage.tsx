import { useState } from "react";
import { Loader2, RefreshCcw } from "lucide-react";
import { callAPI } from "../api";
import { RunsTable } from "../components/RunsTable";
import { localDateTime } from "../time";
import type { AppState, RouteName, SyncRun } from "../types";

type HomePageProps = {
	state: AppState | null;
	onMessage: (message: string) => void;
	onSynced: () => Promise<void>;
	onNavigate: (route: RouteName, path: string) => void;
};

export function HomePage({ state, onMessage, onSynced, onNavigate }: HomePageProps) {
	const [syncing, setSyncing] = useState(false);
	const syncNow = async () => {
		setSyncing(true);
		onMessage("Sync started...");
		try {
			const result = await callAPI<{ run: SyncRun }>("/api/sync", { method: "POST", body: "{}" });
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
				<h1>Sync hourly and all-day Google calendars.</h1>
				<p>Connect Google Calendar, choose your hourly source and all-day companion calendar, then keep both views aligned.</p>
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
			</div>
			{state.pair ? (
				<>
					<div className="calendar-grid">
						<div><span>Hourly calendar</span><strong>{state.pair.timed_calendar_name}</strong></div>
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
