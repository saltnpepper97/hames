export function scarLabel(value: string): string {
  const words = value.replaceAll("_", " ").replace(/^scar\./, "");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function scarDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function shortScarId(value: string): string {
  return value.slice(0, 8);
}
