import type { RouteName } from "./types";

export const routeFromPath = (): RouteName => {
	const path = window.location.pathname;
	if (path.startsWith("/setup")) return "setup";
	if (path.startsWith("/sync-runs")) return "logs";
	if (path.startsWith("/conflicts")) return "conflicts";
	return "home";
};
