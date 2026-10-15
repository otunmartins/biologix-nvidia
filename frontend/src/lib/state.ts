import type { Adapter, LiveResult, State, Verification } from "@/lib/types";

/** Icon glyph per state. A state is never carried by colour alone. */
export const STATE_GLYPH: Record<State, string> = {
  verified: "✓",
  configured: "!",
  failed: "✕",
  inactive: "–",
};

export const STATE_LABEL: Record<State, string> = {
  verified: "verified live",
  configured: "configured, unproven",
  failed: "failed",
  inactive: "inactive",
};

/** Tailwind text colour per state, for the glyph and the dot only. */
export const STATE_COLOR: Record<State, string> = {
  verified: "text-state-good",
  configured: "text-state-warn",
  failed: "text-state-crit",
  inactive: "text-muted",
};

export const STATE_FILL: Record<State, string> = {
  verified: "var(--state-good)",
  configured: "var(--state-warn)",
  failed: "var(--state-crit)",
  inactive: "var(--text-muted)",
};

export function liveResultFor(
  verification: Verification | undefined,
  adapter: string,
): LiveResult | undefined {
  if (!verification?.ran) return undefined;
  return verification.results.find((r) => r.adapter === adapter);
}

/**
 * The honest state of one component.
 *
 * A live call that succeeded is the only thing that earns `verified`. A live
 * call that failed is `failed` even when the adapter reports itself available
 * — which is exactly the case this console exists to stop misreporting.
 */
export function stateOf(adapter: Adapter | undefined, live: LiveResult | undefined): State {
  if (live) return live.ok ? "verified" : "failed";
  if (!adapter) return "inactive";
  if (!adapter.available) return adapter.reason ? "failed" : "inactive";
  return "configured";
}

/** Who is at fault, in the words the backend uses. */
export function faultOf(live: LiveResult | undefined): "upstream" | "ours" | null {
  if (!live || live.ok) return null;
  return live.error === "UpstreamServiceError" ? "upstream" : "ours";
}

export function shortName(adapter: string): string {
  return adapter.replace(/^bionemo-/, "");
}

export function formatSeconds(s: number | undefined): string {
  if (s === undefined) return "—";
  return s < 10 ? `${s.toFixed(2)} s` : `${s.toFixed(1)} s`;
}

export function formatAge(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "age unknown";
  if (hours < 1 / 60) return "seconds ago";
  if (hours < 1) return `${Math.round(hours * 60)} min ago`;
  if (hours < 48) return `${hours.toFixed(1)} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}
