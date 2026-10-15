/** Shared formatting. Every function here returns an em dash rather than
 *  inventing a value when it has nothing to show. */

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = Date.parse(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return "—";
  const seconds = (Date.now() - then) / 1000;
  if (seconds < 0) return "just now";
  if (seconds < 45) return "just now";
  if (seconds < 90) return "a minute ago";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)} h ago`;
  const days = hours / 24;
  if (days < 30) return `${Math.round(days)} d ago`;
  return new Date(then).toISOString().slice(0, 10);
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${iso.slice(11, 19)}Z`;
}

export function formatSeconds(value: number | undefined | null): string {
  if (value === undefined || value === null) return "—";
  return value < 10 ? `${value.toFixed(2)} s` : `${value.toFixed(1)} s`;
}

export function formatNumber(value: unknown, digits = 1): string {
  return typeof value === "number" ? value.toFixed(digits) : "—";
}

export function formatPercent(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

/** A short, readable identifier. Full ids stay available in tooltips. */
export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
