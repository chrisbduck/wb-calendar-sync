export const callAPI = async <T,>(url: string, options?: RequestInit): Promise<T> => {
	const response = await fetch(url, { credentials: "same-origin", headers: { "Content-Type": "application/json", ...(options?.headers || {}) }, ...options });
	const data = await response.json().catch(() => ({}));
	if (!response.ok) {
		throw new Error(data.message || data.error || `Request failed: ${response.status}`);
	}
	return data as T;
};
