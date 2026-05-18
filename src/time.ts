export const localDateTime = (value: string | null) => {
	if (!value) return "Not yet";
	return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
};
