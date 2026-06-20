import { LogIn } from "lucide-react";

export const GOOGLE_RECONNECT_MESSAGE = "Google authorization expired or was revoked. Reconnect Google to continue syncing.";

export function needsGoogleReconnect(message: string | null | undefined) {
	return Boolean(message && message.includes(GOOGLE_RECONNECT_MESSAGE));
}

export function ReconnectGoogle({ message }: { message: string | null | undefined }) {
	if (!needsGoogleReconnect(message)) return <>{message || ""}</>;
	return (
		<div className="reconnect-google">
			<span>{GOOGLE_RECONNECT_MESSAGE}</span>
			<a className="secondary-button compact-button" href="/auth/start"><LogIn size={16} /> Reconnect Google</a>
		</div>
	);
}
