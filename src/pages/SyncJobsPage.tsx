import React, { useCallback, useEffect, useState } from "react";
import { Play, Power, PowerOff, Trash2 } from "lucide-react";
import { callAPI } from "../api";
import { localDateTime } from "../time";
import type { AppState, Pair, SyncJob } from "../types";

type SyncJobsPageProps = {
	state: AppState | null;
	onMessage: (message: string) => void;
};

const formForPair = (pair: Pair | null | undefined) => ({
	friendly_name: pair ? `${pair.timed_calendar_name} and ${pair.allday_calendar_name}` : "",
	source_calendar_id: pair?.timed_calendar_id || "",
	target_calendar_id: pair?.allday_calendar_id || "",
});

export function SyncJobsPage({ state, onMessage }: SyncJobsPageProps) {
	const [jobs, setJobs] = useState<SyncJob[]>([]);
	const [form, setForm] = useState(formForPair(state?.pair));
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [running, setRunning] = useState(false);
	const [updatingJobId, setUpdatingJobId] = useState<number | null>(null);

	const loadJobs = useCallback(async () => {
		const data = await callAPI<{ jobs: SyncJob[] }>("/api/sync-jobs");
		setJobs(data.jobs);
	}, []);

	useEffect(() => {
		if (!state?.user) {
			setLoading(false);
			return;
		}
		loadJobs().catch((error) => onMessage(error.message)).finally(() => setLoading(false));
	}, [loadJobs, onMessage, state?.user]);

	useEffect(() => {
		setForm((current) => {
			const next = formForPair(state?.pair);
			return { ...current, source_calendar_id: next.source_calendar_id, target_calendar_id: next.target_calendar_id, friendly_name: current.friendly_name || next.friendly_name };
		});
	}, [state?.pair]);

	if (!state?.user) return <section className="panel"><p>Please sign in before managing sync jobs.</p><a className="primary-button" href="/auth/start">Sign in with Google</a></section>;
	if (!state.pair) return <section className="panel"><p>Choose calendars on the setup page before creating sync jobs.</p><a className="primary-button" href="/setup">Choose calendars</a></section>;

	const createJob = async (event: React.FormEvent) => {
		event.preventDefault();
		setSaving(true);
		try {
			const data = await callAPI<{ job: SyncJob }>("/api/sync-jobs", { method: "POST", body: JSON.stringify(form) });
			setJobs((current) => [data.job, ...current]);
			setForm(formForPair(state.pair));
			onMessage("Sync job created.");
		} catch (error) {
			onMessage(error instanceof Error ? error.message : "Could not create sync job.");
		} finally {
			setSaving(false);
		}
	};

	const deleteJob = async (job: SyncJob) => {
		await callAPI("/api/sync-jobs/" + job.id, { method: "DELETE" });
		setJobs((current) => current.filter((item) => item.id !== job.id));
		onMessage("Sync job deleted.");
	};

	const toggleJob = async (job: SyncJob) => {
		setUpdatingJobId(job.id);
		try {
			const data = await callAPI<{ job: SyncJob }>("/api/sync-jobs/" + job.id, { method: "PATCH", body: JSON.stringify({ enabled: !job.enabled }) });
			setJobs((current) => current.map((item) => item.id === job.id ? data.job : item));
			onMessage(data.job.enabled ? "Sync job enabled." : "Sync job disabled.");
		} catch (error) {
			onMessage(error instanceof Error ? error.message : "Could not update sync job.");
		} finally {
			setUpdatingJobId(null);
		}
	};

	const runJobs = async () => {
		setRunning(true);
		try {
			const data = await callAPI<{ result: { total: number; succeeded: number; failed: number } }>("/api/sync-jobs/run-all", { method: "POST" });
			await loadJobs();
			onMessage(`Ran ${data.result.total} enabled jobs: ${data.result.succeeded} succeeded, ${data.result.failed} failed.`);
		} catch (error) {
			onMessage(error instanceof Error ? error.message : "Could not run sync jobs.");
		} finally {
			setRunning(false);
		}
	};

	return (
		<section className="panel form-panel">
			<div className="section-heading">
				<div>
					<h1>Sync jobs</h1>
					<p>Create named two-way calendar sync jobs and run the enabled set manually.</p>
				</div>
				<button className="secondary-button" onClick={runJobs} disabled={running}><Play size={16} /> {running ? "Running..." : "Run enabled jobs"}</button>
			</div>
			<form onSubmit={createJob}>
				<label>Friendly name<input value={form.friendly_name} onChange={(event) => setForm({ ...form, friendly_name: event.target.value })} required /></label>
				<div className="calendar-grid selected-calendars">
					<div><span>Hourly calendar</span><strong>{state.pair.timed_calendar_name}</strong><code>{state.pair.timed_calendar_id}</code></div>
					<div><span>Daily calendar</span><strong>{state.pair.allday_calendar_name}</strong><code>{state.pair.allday_calendar_id}</code></div>
				</div>
				<button className="primary-button" disabled={saving}>{saving ? "Creating..." : "Create sync job"}</button>
			</form>
			<h2>Existing jobs</h2>
			{loading ? <p>Loading jobs...</p> : jobs.length === 0 ? <div className="empty-state">No sync jobs yet.</div> : (
				<div className="table-wrap">
					<table>
						<thead><tr><th>Name</th><th>Hourly</th><th>Daily</th><th>Enabled</th><th>Last run</th><th>Status</th><th>Error</th><th></th></tr></thead>
						<tbody>
							{jobs.map((job) => <tr key={job.id}>
								<td><strong>{job.friendly_name}</strong></td>
								<td className="calendar-id">{job.source_calendar_id}</td>
								<td className="calendar-id">{job.target_calendar_id}</td>
								<td><button className={`toggle-button ${job.enabled ? "enabled" : "disabled"}`} onClick={() => toggleJob(job)} disabled={updatingJobId === job.id} aria-label={`${job.enabled ? "Disable" : "Enable"} ${job.friendly_name}`}>{job.enabled ? <Power size={16} /> : <PowerOff size={16} />} {job.enabled ? "Enabled" : "Disabled"}</button></td>
								<td>{localDateTime(job.last_run_at)}</td>
								<td>{job.last_status ? <span className={`status ${job.last_status}`}>{job.last_status}</span> : "Not yet"}</td>
								<td>{job.last_error || ""}</td>
								<td><button className="icon-button danger-button" aria-label={`Delete ${job.friendly_name}`} onClick={() => deleteJob(job)}><Trash2 size={16} /></button></td>
							</tr>)}
						</tbody>
					</table>
				</div>
			)}
		</section>
	);
}
