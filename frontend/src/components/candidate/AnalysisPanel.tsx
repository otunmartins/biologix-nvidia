"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { Alert, Button, EvidenceTag, Field, Meter, inputClass } from "@/components/ui/Primitives";
import { ComplexViewer } from "@/components/structure/ComplexViewer";
import { JOB_ENGINE, JOB_LABEL, Spinner, StatusBadge } from "@/components/ui/Status";
import { get, post } from "@/lib/client";
import { formatNumber, formatRelative } from "@/lib/format";
import type { Job, JobKind, StructureArtifact } from "@/lib/types";

const POLL_MS = 2_500;

/**
 * The expensive NVIDIA work, per candidate.
 *
 * Both analyses are offered the same way, and both are honest about what they
 * are. Each result arrives with the caveat the backend stored alongside it,
 * and that caveat is rendered as part of the result rather than beneath it —
 * a docking confidence or an affinity figure read without it becomes a
 * binding claim, which is the single most likely way this product could
 * mislead someone.
 */

const ANALYSIS: Record<JobKind, { title: string; blurb: string; gives: string }> = {
  dock: {
    title: "Dock to the target",
    blurb:
      "DiffDock places the capped oligomer against the prepared target structure. The poses are starting geometries for simulation, not affinity estimates.",
    gives: "poses + per-pose confidence",
  },
  affinity: {
    title: "Predict the complex",
    blurb:
      "Boltz-2 predicts a biologic–oligomer complex and an affinity figure. The affinity head was trained on small-molecule binding, so an oligomer is out of distribution by construction.",
    gives: "structure + affinity, with its ceiling stated",
  },
  simulate: {
    title: "Simulate the complex",
    blurb:
      "OpenMM runs implicit-solvent dynamics of the protein with the oligomer held near it. A few picoseconds illustrates the complex and samples local contacts; it measures nothing about aggregation, which takes many orders of magnitude longer.",
    gives: "an animated trajectory + interface contacts",
  },
};

export function AnalysisPanel({
  runId,
  candidateId,
  initialJobs,
  dpSuggestion,
  unavailable,
}: {
  runId: string;
  candidateId: string;
  initialJobs: Job[];
  dpSuggestion: number;
  /** Adapter name → why it cannot run right now, from /availability. */
  unavailable: Record<string, string | null>;
}) {
  const [jobs, setJobs] = useState(initialJobs);
  const [kind, setKind] = useState<JobKind>("dock");
  const [dp, setDp] = useState(Math.min(dpSuggestion, 25));
  const [poses, setPoses] = useState(5);
  const [steps, setSteps] = useState(6000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const live = jobs.some((job) => job.status === "queued" || job.status === "running");

  useEffect(() => {
    if (!live) return;
    const id = setInterval(async () => {
      const result = await get<{ jobs: Job[] }>(
        `/jobs?run_id=${runId}&candidate_id=${candidateId}&limit=40`,
      );
      if (result.ok) setJobs(result.data.jobs);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [live, runId, candidateId]);

  // Simulation runs locally on the CPU, so no NIM can block it.
  const blockedBy =
    kind === "dock"
      ? unavailable["diffdock"]
      : kind === "affinity"
        ? unavailable["boltz2"]
        : null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await post<{ job_id: string }>(
      `/runs/${runId}/candidates/${candidateId}/analyses`,
      { kind, degree_of_polymerisation: dp, num_poses: poses, production_steps: steps },
    );
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const refreshed = await get<{ jobs: Job[] }>(
      `/jobs?run_id=${runId}&candidate_id=${candidateId}&limit=40`,
    );
    if (refreshed.ok) setJobs(refreshed.data.jobs);
  }

  return (
    <div className="space-y-5">
      <form onSubmit={submit} className="space-y-4">
        <fieldset>
          <legend className="text-2xs uppercase tracking-wider text-muted">Analysis</legend>
          <div className="mt-2.5 grid gap-3 sm:grid-cols-2">
            {(Object.keys(ANALYSIS) as JobKind[]).map((option) => {
              const spec = ANALYSIS[option];
              const selected = kind === option;
              return (
                <motion.label
                  key={option}
                  whileHover={{ y: -2 }}
                  transition={{ duration: 0.16 }}
                  className={`relative cursor-pointer rounded-xl border p-4 transition-colors ${
                    selected
                      ? "border-accent/60 bg-accent-soft"
                      : "border-hairline bg-surface-2 hover:border-hairline-strong"
                  }`}
                >
                  <input
                    type="radio"
                    name="analysis"
                    value={option}
                    checked={selected}
                    onChange={() => setKind(option)}
                    className="absolute right-4 top-4 accent-[var(--accent)]"
                  />
                  <span className="block pr-6 text-sm text-ink">{spec.title}</span>
                  <span className="mt-0.5 block font-mono text-2xs text-accent">
                    {JOB_ENGINE[option]}
                  </span>
                  <span className="mt-2.5 block text-2xs leading-relaxed text-muted">
                    {spec.blurb}
                  </span>
                  <span className="mt-2 block text-2xs text-ink-2">Returns {spec.gives}</span>
                </motion.label>
              );
            })}
          </div>
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Degree of polymerisation"
            htmlFor="analysis-dp"
            hint="The oligomer is built explicitly at this length. Longer is more realistic and much harder to dock."
          >
            <input
              id="analysis-dp"
              type="number"
              min={1}
              max={200}
              value={dp}
              onChange={(e) => setDp(Number(e.target.value))}
              className={`${inputClass} tabular`}
            />
          </Field>

          {kind === "dock" && (
            <Field label="Poses" htmlFor="analysis-poses" hint="1–20 per request.">
              <input
                id="analysis-poses"
                type="number"
                min={1}
                max={20}
                value={poses}
                onChange={(e) => setPoses(Number(e.target.value))}
                className={`${inputClass} tabular`}
              />
            </Field>
          )}

          {kind === "simulate" && (
            <Field
              label="Production steps"
              htmlFor="analysis-steps"
              hint="At a 2 fs timestep. 6000 steps is 12 ps and takes about a minute on CPU."
            >
              <input
                id="analysis-steps"
                type="number"
                min={500}
                max={200000}
                step={500}
                value={steps}
                onChange={(e) => setSteps(Number(e.target.value))}
                className={`${inputClass} tabular`}
              />
            </Field>
          )}

          <div className="flex items-end">
            <Button type="submit" disabled={busy} className="w-full">
              {busy ? "queueing…" : `run ${JOB_LABEL[kind]}`}
            </Button>
          </div>
        </div>

        {blockedBy && (
          <Alert kind="warn" title="this will probably fail">
            {blockedBy} The job is still created, and the adapter&apos;s reason and remedy are
            stored with it.
          </Alert>
        )}
        {error && <Alert title="refused">{error}</Alert>}
      </form>

      <div className="space-y-3 border-t border-hairline pt-5">
        <h3 className="text-2xs uppercase tracking-wider text-muted">
          Jobs on this candidate
        </h3>
        {jobs.length === 0 ? (
          <p className="text-2xs text-muted">
            None yet. Nothing above has been run for this candidate.
          </p>
        ) : (
          <ul className="space-y-3">
            <AnimatePresence initial={false}>
              {jobs.map((job) => (
                <motion.li
                  key={job.id}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.22 }}
                >
                  <JobCard job={job} />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
  const running = job.status === "queued" || job.status === "running";
  return (
    <div className="rounded-xl border border-hairline bg-surface-2 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            {running && <Spinner size={12} />}
            <span className="text-xs text-ink">{JOB_LABEL[job.kind] ?? job.kind}</span>
            <StatusBadge status={job.status} stage={running ? job.stage : null} />
          </div>
          <p className="mt-1 font-mono text-2xs text-muted">
            {JOB_ENGINE[job.kind] ?? job.kind} · DP{" "}
            {String(job.payload.degree_of_polymerisation ?? "—")}
            {job.payload.num_poses ? ` · ${String(job.payload.num_poses)} poses` : ""}
          </p>
        </div>
        <div className="text-right">
          <EvidenceTag tier={job.evidence} />
          <p className="mt-1 text-2xs text-muted">
            {job.created_by_name ? `${job.created_by_name} · ` : ""}
            {formatRelative(job.created_at)}
          </p>
        </div>
      </div>

      {running && <Meter value={job.progress} className="mt-3" />}

      {job.status === "failed" && (
        <div className="mt-3 rounded-lg border border-state-crit/30 bg-state-crit/5 px-3 py-2.5">
          <p className="text-2xs leading-relaxed text-ink-2">
            <span className="font-mono text-state-crit">{job.error_kind ?? "failed"}</span> —{" "}
            {job.error}
          </p>
          {job.remedy && (
            <p className="mt-1.5 text-2xs leading-relaxed text-muted">
              What to do: {job.remedy}
            </p>
          )}
        </div>
      )}

      {job.status === "succeeded" && job.result && <JobResult job={job} />}

      {job.status === "succeeded" && artifactsOf(job).length > 0 && (
        <div className="mt-4">
          <ComplexViewer job={job} artifacts={artifactsOf(job)} />
        </div>
      )}
    </div>
  );
}

/** The coordinate files a finished job wrote, if any. */
function artifactsOf(job: Job): StructureArtifact[] {
  const artifacts = job.result?.artifacts;
  return Array.isArray(artifacts) ? (artifacts as StructureArtifact[]) : [];
}

/**
 * A finished result.
 *
 * Only fields the backend actually returned are shown; anything absent is an
 * em dash rather than a zero. The stored caveat is rendered as part of the
 * result block, not as a footnote.
 */
function JobResult({ job }: { job: Job }) {
  const result = job.result ?? {};
  const caveat = typeof result.caveat === "string" ? result.caveat : null;
  const numbers = pickNumbers(result);

  return (
    <div className="mt-3">
      {numbers.length > 0 && (
        <dl className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {numbers.map(([label, value]) => (
            <div key={label}>
              <dt className="text-2xs uppercase tracking-wider text-muted">{label}</dt>
              <dd className="tabular mt-0.5 font-mono text-xs text-ink">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {typeof result.out_of_distribution === "boolean" && result.out_of_distribution && (
        <p className="mt-3 rounded-lg border border-state-warn/30 bg-state-warn/5 px-3 py-2 text-2xs leading-relaxed text-ink-2">
          <span className="font-mono text-state-warn">out of distribution</span> — the model was
          not trained on anything like this query, so the figure above ranks candidates under one
          fixed model and means nothing in absolute terms.
        </p>
      )}

      {caveat && (
        <p className="mt-3 border-t border-hairline pt-2.5 text-2xs leading-relaxed text-muted">
          {caveat}
        </p>
      )}
    </div>
  );
}

/** The scalar fields worth surfacing, with readable labels. */
const RESULT_LABELS: Record<string, string> = {
  n_poses: "poses returned",
  n_poses_parsed: "poses parsed",
  best_confidence: "best confidence",
  mean_confidence: "mean confidence",
  rotatable_bonds: "rotatable bonds",
  affinity: "affinity",
  affinity_probability: "binder probability",
  affinity_pred_value: "affinity (model units)",
  confidence_score: "complex confidence",
  iptm: "ipTM",
  ptm: "pTM",
  plddt: "pLDDT",
  n_structures: "structures",
  screening_dp: "oligomer DP",
  degree_of_polymerisation: "oligomer DP",
  seconds: "runtime (s)",
  n_replicates: "replicates",
  n_successful: "replicates that ran",
  n_heavy_contacts: "heavy-atom contacts",
  n_hydrogen_bonds: "hydrogen bonds",
  min_distance_nm: "closest approach (nm)",
  protein_ca_rmsd_nm: "protein Cα RMSD (nm)",
  total_kj_per_mol: "interaction energy (kJ/mol)",
  production_ps: "production (ps)",
};

function pickNumbers(result: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = [];
  for (const [key, label] of Object.entries(RESULT_LABELS)) {
    const value = result[key];
    if (typeof value === "number") {
      out.push([label, Number.isInteger(value) ? String(value) : formatNumber(value, 3)]);
    }
  }
  if (typeof result.target === "string") out.push(["target", result.target]);
  return out;
}
