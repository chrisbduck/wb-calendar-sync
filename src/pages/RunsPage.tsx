import { useEffect, useState } from "react";
import { callAPI } from "../api";
import { RunsTable } from "../components/RunsTable";
import type { SyncRun } from "../types";

export function RunsPage() {
	const [runs, setRuns] = useState<SyncRun[]>([]);
	useEffect(() => {
		callAPI<{ runs: SyncRun[] }>("/api/sync-runs").then((data) => setRuns(data.runs));
	}, []);
	return <section className="panel">
		<h1>Sync logs</h1>
		<RunsTable runs={runs} />
	</section>;
}
