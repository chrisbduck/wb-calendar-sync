import { useEffect, useState } from "react";
import { callAPI } from "../api";
import { localDateTime } from "../time";
import type { Conflict } from "../types";

export function ConflictsPage() {
	const [conflicts, setConflicts] = useState<Conflict[]>([]);
	useEffect(() => {
		callAPI<{ conflicts: Conflict[] }>("/api/conflicts").then((data) =>
			setConflicts(data.conflicts),
		);
	}, []);
	return (
		<section className="panel">
			<h1>Conflicts</h1>
			<table>
				<thead>
					<tr>
						<th>Created</th>
						<th>Timed event</th>
						<th>All-day event</th>
						<th>Reason</th>
					</tr>
				</thead>
				<tbody>
					{conflicts.length ? (
						conflicts.map((conflict) => (
							<tr key={conflict.id}>
								<td>{localDateTime(conflict.created_at)}</td>
								<td>{conflict.timed_event_id || ""}</td>
								<td>{conflict.allday_event_id || ""}</td>
								<td>{conflict.reason}</td>
							</tr>
						))
					) : (
						<tr>
							<td colSpan={4}>No conflicts.</td>
						</tr>
					)}
				</tbody>
			</table>
		</section>
	);
}
