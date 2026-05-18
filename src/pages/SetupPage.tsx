import React, { useEffect, useState } from "react";
import { callAPI } from "../api";
import type { AppState, CalendarOption } from "../types";

type SetupPageProps = {
	state: AppState | null;
	onMessage: (message: string) => void;
	onSaved: () => void;
};

export function SetupPage({ state, onMessage, onSaved }: SetupPageProps) {
	const [calendars, setCalendars] = useState<CalendarOption[]>([]);
	const [timed, setTimed] = useState("");
	const [allDay, setAllDay] = useState("");
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		callAPI<{ calendars: CalendarOption[]; pair: { timed_calendar_id: string; allday_calendar_id: string } | null }>("/api/calendars")
			.then((data) => {
				setCalendars(data.calendars);
				setTimed(data.pair?.timed_calendar_id || data.calendars[0]?.id || "");
				setAllDay(data.pair?.allday_calendar_id || data.calendars[1]?.id || data.calendars[0]?.id || "");
			})
			.catch((error) => onMessage(error.message));
	}, [onMessage]);

	if (!state?.user) return <section className="panel"><p>Please sign in before choosing calendars.</p><a className="primary-button" href="/auth/start">Sign in with Google</a></section>;

	const save = async (event: React.FormEvent) => {
		event.preventDefault();
		setSaving(true);
		onMessage("Saving setup...");
		try {
			await callAPI("/api/setup", { method: "POST", body: JSON.stringify({ timed_calendar_id: timed, allday_calendar_id: allDay }) });
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
				<label>Hourly calendar<select value={timed} onChange={(event) => setTimed(event.target.value)}>{calendars.map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.label}</option>)}</select></label>
				<label>All-day calendar<select value={allDay} onChange={(event) => setAllDay(event.target.value)}>{calendars.map((calendar) => <option key={calendar.id} value={calendar.id}>{calendar.label}</option>)}</select></label>
				<button className="primary-button" disabled={saving}>{saving ? "Saving..." : "Save setup"}</button>
			</form>
		</section>
	);
}
