"use client";

import { motion } from "framer-motion";
import type { Job, JobStatus, RunStatus, Verdict } from "@/lib/types";

/**
 * Run, job and decision states.
 *
 * Each is three channels at once — a dot, a glyph, a word — and only the
 * states that are genuinely in flight animate. A finished thing is still, so
 * movement on this page always means "something is happening right now".
 */

const RUN_TONE: Record<RunStatus, { color: string; fill: string; glyph: string }> = {
  queued: { color: "text-muted", fill: "var(--text-muted)", glyph: "·" },
  running: { color: "text-accent", fill: "var(--accent)", glyph: "»" },
  succeeded: { color: "text-state-good", fill: "var(--state-good)", glyph: "✓" },
  failed: { color: "text-state-crit", fill: "var(--state-crit)", glyph: "✕" },
};

export function StateDot({
  status,
  className = "",
}: {
  status: JobStatus | RunStatus;
  className?: string;
}) {
  const tone = RUN_TONE[status];
  const live = status === "running";
  return (
    <span
      aria-hidden
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${live ? "breathe" : ""} ${className}`}
      style={{ background: tone.fill }}
    />
  );
}

export function StatusBadge({
  status,
  stage,
  className = "",
}: {
  status: JobStatus | RunStatus;
  stage?: string | null;
  className?: string;
}) {
  const tone = RUN_TONE[status];
  const extra = stage && stage !== status ? stage : null;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap ${className}`}>
      <StateDot status={status} />
      <span aria-hidden className={`font-mono text-2xs leading-none ${tone.color}`}>
        {tone.glyph}
      </span>
      <span className="text-2xs uppercase tracking-wider text-ink-2">
        {status}
        {extra && <span className="text-muted"> · {extra}</span>}
      </span>
    </span>
  );
}

/** A ring that sweeps while work is in flight, and stops when it is not. */
export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span
      aria-hidden
      className="sweep inline-block shrink-0 rounded-full border-2 border-grid"
      style={{
        width: size,
        height: size,
        borderTopColor: "var(--accent)",
      }}
    />
  );
}

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const shortlisted = verdict === "shortlisted";
  return (
    <motion.span
      initial={{ scale: 0.92, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 420, damping: 26 }}
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 ${
        shortlisted
          ? "border-state-good/40 bg-state-good/10"
          : "border-state-crit/40 bg-state-crit/10"
      }`}
    >
      <span
        aria-hidden
        className={`font-mono text-2xs leading-none ${
          shortlisted ? "text-state-good" : "text-state-crit"
        }`}
      >
        {shortlisted ? "★" : "✕"}
      </span>
      <span className="text-2xs uppercase tracking-wider text-ink-2">{verdict}</span>
    </motion.span>
  );
}

export const JOB_LABEL: Record<string, string> = {
  dock: "DiffDock pose",
  affinity: "Boltz-2 affinity",
  simulate: "MD trajectory",
};

/** What ran it, named by product rather than by filename. */
export const JOB_ENGINE: Record<string, string> = {
  dock: "DiffDock 2.1.0 · BioNeMo NIM",
  affinity: "Boltz-2 · BioNeMo NIM",
  simulate: "OpenMM · CPU or CUDA platform",
};

export function JobChip({ job }: { job: Job }) {
  const live = job.status === "queued" || job.status === "running";
  return (
    <span
      title={
        job.status === "failed"
          ? `${job.error ?? "failed"}${job.remedy ? ` — ${job.remedy}` : ""}`
          : (job.stage ?? job.status)
      }
      className="inline-flex items-center gap-1.5 rounded border border-hairline bg-surface-2 px-2 py-0.5"
    >
      {live ? <Spinner size={10} /> : <StateDot status={job.status} />}
      <span className="text-2xs uppercase tracking-wider text-ink-2">
        {JOB_LABEL[job.kind] ?? job.kind}
      </span>
    </span>
  );
}
