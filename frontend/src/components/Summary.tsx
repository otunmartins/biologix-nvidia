import { faultOf, formatAge } from "@/lib/state";
import type { Availability, Verification } from "@/lib/types";

/** A stat tile: label, value, and one line of context. No invented trend. */
function Tile({
  label,
  value,
  context,
  tone,
}: {
  label: string;
  value: string;
  context: string;
  tone?: "good" | "warn" | "crit";
}) {
  const color =
    tone === "good"
      ? "text-state-good"
      : tone === "warn"
        ? "text-state-warn"
        : tone === "crit"
          ? "text-state-crit"
          : "text-ink";
  return (
    <div className="rounded-lg border border-hairline bg-surface px-4 py-3.5">
      <p className="text-2xs uppercase tracking-wider text-muted">{label}</p>
      <p className={`mt-1.5 text-2xl font-semibold leading-none ${color}`}>{value}</p>
      <p className="mt-2 text-2xs leading-relaxed text-muted">{context}</p>
    </div>
  );
}

export function Summary({
  availability,
  verification,
}: {
  availability?: Availability;
  verification?: Verification;
}) {
  const nims = (availability?.adapters ?? []).filter((a) => a.adapter.startsWith("bionemo-"));
  const results = verification?.ran ? verification.results : [];
  const verified = results.filter((r) => r.ok);
  const failed = results.filter((r) => !r.ok);
  const upstream = failed.filter((r) => faultOf(r) === "upstream");
  const untested = nims.filter((a) => !results.some((r) => r.adapter === a.adapter));

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)]">
      {/* The hero figure: the one number this console leads with. */}
      <div className="rounded-lg border border-hairline bg-surface p-6">
        <p className="text-2xs uppercase tracking-[0.16em] text-muted">BioNeMo NIMs verified live</p>
        <p className="mt-3 text-6xl font-semibold leading-none text-ink">
          {verified.length}
          <span className="text-2xl font-normal text-muted"> / {nims.length}</span>
        </p>
        <p className="mt-4 text-xs leading-relaxed text-ink-2">
          {verification?.ran ? (
            <>
              Measured by a real call to each endpoint,{" "}
              <span className="text-ink">{formatAge(verification.age_hours)}</span>.
              {verification.stale && (
                <span className="text-state-warn"> Older than a day — re-run before trusting it.</span>
              )}
            </>
          ) : (
            <span className="text-state-warn">
              No live check has run. Nothing on this page has been proven to work.
            </span>
          )}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <Tile
          label="Configured"
          value={`${availability?.summary.n_available ?? 0} / ${availability?.summary.n_total ?? 0}`}
          context="Credentials present and an endpoint set. Not a claim that anything works."
        />
        <Tile
          label="Answered a call"
          value={String(verified.length)}
          context={
            verified.length > 0
              ? `${verified.map((r) => r.adapter.replace("bionemo-", "")).join(", ")} returned HTTP 200.`
              : "No component has returned a successful response."
          }
          tone={verified.length > 0 ? "good" : "warn"}
        />
        <Tile
          label="Failed"
          value={String(failed.length)}
          context={
            failed.length === 0
              ? "No failures recorded in the last check."
              : `${upstream.length} upstream (the provider's own fault), ${failed.length - upstream.length} ours to fix.`
          }
          tone={failed.length > 0 ? "crit" : "good"}
        />
        <Tile
          label="Never called"
          value={String(untested.length)}
          context={
            untested.length === 0
              ? "Every NIM has been exercised at least once."
              : `${untested.map((a) => a.adapter.replace("bionemo-", "")).join(", ")} has no live result.`
          }
          tone={untested.length > 0 ? "warn" : "good"}
        />
        <Tile
          label="Simulation"
          value={String(availability?.simulation?.platform ?? availability?.simulation?.backend ?? "—")}
          context="OpenMM resolves to CPU here; no CUDA runtime is present on this machine."
        />
        <Tile
          label="Unverified paths"
          value={String(availability?.summary.unverified_adapters.length ?? 0)}
          context="Components whose code path has never run against a live endpoint or a GPU."
          tone={(availability?.summary.unverified_adapters.length ?? 0) > 0 ? "warn" : "good"}
        />
      </div>
    </div>
  );
}
