export const words = (value: string) => value.replaceAll("_", " ").replaceAll("-", " ");

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1) return "<1 ms";
  if (milliseconds < 1_000) return `${Math.round(milliseconds)} ms`;
  const seconds = milliseconds / 1_000;
  return `${seconds < 10 ? seconds.toFixed(2) : seconds.toFixed(1)} s`;
}

export const formatPercent = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? "n/a" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;

export const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? "n/a" : value.toFixed(digits);

/** "2025-01-27" -> "Jan 27, 2025" without timezone drift. */
export function dateLabel(value: string): string {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, day)));
}
