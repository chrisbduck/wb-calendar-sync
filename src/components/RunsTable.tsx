import { localDateTime } from "../time";
import type { SyncRun } from "../types";

export function RunsTable({ runs }: { runs: SyncRun[] }) {
	return <table><thead><tr><th>Started</th><th>Finished</th><th>Status</th><th>Message</th></tr></thead><tbody>{runs.length ? runs.map((run) => <tr key={run.id}><td>{localDateTime(run.started_at)}</td><td>{localDateTime(run.finished_at)}</td><td><span className={`status ${run.status}`}>{run.status}</span></td><td>{run.message || ""}</td></tr>) : <tr><td colSpan={4}>No sync runs yet.</td></tr>}</tbody></table>;
}
