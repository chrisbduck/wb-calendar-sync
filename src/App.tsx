import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarDays, CheckCircle2, Home, ListChecks, LogOut, RefreshCcw, Settings, TriangleAlert } from "lucide-react";
import { callAPI } from "./api";
import { LoadingPanel } from "./components/LoadingPanel";
import { routeFromPath } from "./routing";
import { ConflictsPage } from "./pages/ConflictsPage";
import { HomePage } from "./pages/HomePage";
import { RunsPage } from "./pages/RunsPage";
import { SetupPage } from "./pages/SetupPage";
import { SyncJobsPage } from "./pages/SyncJobsPage";
import type { AppState, RouteName } from "./types";

export function App() {
	const [route, setRoute] = useState<RouteName>(routeFromPath());
	const [state, setState] = useState<AppState | null>(null);
	const [loading, setLoading] = useState(true);
	const [message, setMessage] = useState(new URLSearchParams(window.location.search).get("message") || "");

	const refreshState = useCallback(async () => {
		const next = await callAPI<AppState>("/api/app-state");
		setState(next);
	}, []);

	useEffect(() => {
		refreshState().catch((error) => setMessage(error.message)).finally(() => setLoading(false));
	}, [refreshState]);

	const navigate = useCallback((nextRoute: RouteName, path: string) => {
		window.history.pushState({}, "", path);
		setRoute(nextRoute);
		setMessage("");
	}, []);

	useEffect(() => {
		const onPopState = () => setRoute(routeFromPath());
		window.addEventListener("popstate", onPopState);
		return () => window.removeEventListener("popstate", onPopState);
	}, []);

	const content = useMemo(() => {
		if (loading) return <LoadingPanel />;
		if (route === "setup") return <SetupPage state={state} onMessage={setMessage} onSaved={() => { setMessage("Calendar pair saved."); refreshState(); navigate("home", "/"); }} />;
		if (route === "syncJobs") return <SyncJobsPage state={state} onMessage={setMessage} />;
		if (route === "logs") return <RunsPage />;
		if (route === "conflicts") return <ConflictsPage />;
		return <HomePage state={state} onMessage={setMessage} onSynced={refreshState} onNavigate={navigate} />;
	}, [loading, navigate, refreshState, route, state]);

	return (
		<div className="app-shell">
			<header className="topbar">
				<div className="brand"><CalendarDays size={22} /> WB Calendar Sync</div>
				<nav>
					<button className="nav-button" onClick={() => navigate("home", "/")}><Home size={16} /> Home</button>
					<button className="nav-button" onClick={() => navigate("setup", "/setup")}><Settings size={16} /> Setup</button>
					<button className="nav-button" onClick={() => navigate("syncJobs", "/sync-jobs")}><ListChecks size={16} /> Jobs</button>
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
