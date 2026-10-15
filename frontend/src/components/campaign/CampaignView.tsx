"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CampaignProgress } from "@/components/campaign/CampaignProgress";
import { TriageTable } from "@/components/campaign/TriageTable";
import { Weights } from "@/components/campaign/Weights";
import { Alert, Chip, EvidenceTag, Panel, Stat } from "@/components/ui/Primitives";
import { StatusBadge } from "@/components/ui/Status";
import { get, weightQuery } from "@/lib/client";
import { formatPercent, formatRelative } from "@/lib/format";
import type { CampaignDetail, DecisionRecord, Triage } from "@/lib/types";

const POLL_MS = 2_000;

/**
 * One campaign, from in-flight to triaged.
 *
 * While the campaign is working, this follows the run itself. Once it is
 * finished, the only thing that refetches is the ordering — and only when the
 * reader moves a weight. That split is the point: the campaign is a fixed
 * record, and the ordering is not part of it.
 */
export function CampaignView({
  initialRun,
  initialTriage,
  triageError,
}: {
  initialRun: CampaignDetail;
  initialTriage: Triage | null;
  triageError: string | null;
}) {
  const [run, setRun] = useState(initialRun);
  const [triage, setTriage] = useState(initialTriage);
  const [error, setError] = useState<string | null>(triageError);
  const [weights, setWeights] = useState<Record<string, number>>(
    () => initialTriage?.weights ?? {},
  );
  const [passedOnly, setPassedOnly] = useState(true);
  const [busy, setBusy] = useState(false);

  const inFlight = run.status === "queued" || run.status === "running";

  useEffect(() => {
    if (!inFlight) return;
    const id = setInterval(async () => {
      // Judged by HTTP status, never by the presence of `error`: a failed
      // campaign is a successful fetch whose payload carries the reason.
      const result = await get<CampaignDetail>(`/runs/${run.id}`);
      if (result.ok) setRun(result.data);
    }, POLL_MS);
    return () => clearInterval(id);
  }, [inFlight, run.id]);

  const loadTriage = useCallback(
    async (nextWeights: Record<string, number>, nextPassedOnly: boolean) => {
      setBusy(true);
      const query = new URLSearchParams({ passed_only: String(nextPassedOnly) });
      const encoded = weightQuery(nextWeights);
      if (encoded) query.set("weights", encoded);
      const result = await get<Triage>(`/runs/${run.id}/ranking?${query}`);
      setBusy(false);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setError(null);
      setTriage(result.data);
    },
    [run.id],
  );

  // A campaign that finishes while the page is open now has something to rank.
  useEffect(() => {
    if (run.status === "succeeded" && !triage) void loadTriage(weights, passedOnly);
  }, [run.status, triage, loadTriage, weights, passedOnly]);

  const criterionLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const criterion of triage?.criteria ?? []) labels[criterion.key] = criterion.label;
    return labels;
  }, [triage]);

  function changeWeight(key: string, value: number) {
    const next = { ...weights, [key]: value };
    setWeights(next);
    if (Object.values(next).every((v) => v <= 0)) {
      // The API refuses an all-zero ordering, so the request is not sent and
      // the previous ordering stands.
      setError("At least one criterion must carry a weight above zero.");
      return;
    }
    void loadTriage(next, passedOnly);
  }

  function resetWeights() {
    const defaults: Record<string, number> = {};
    for (const criterion of triage?.criteria ?? []) {
      defaults[criterion.key] = criterion.default_weight;
    }
    setWeights(defaults);
    void loadTriage(defaults, passedOnly);
  }

  function togglePassedOnly() {
    const next = !passedOnly;
    setPassedOnly(next);
    void loadTriage(weights, next);
  }

  /** A decision made in the table is reflected locally rather than refetched:
   *  the ordering has not changed, only the reader's own verdict. */
  function applyDecision(candidateId: string, decision: DecisionRecord | null) {
    setTriage((current) =>
      current
        ? {
            ...current,
            candidates: current.candidates.map((row) =>
              row.candidate_id === candidateId ? { ...row, my_decision: decision } : row,
            ),
          }
        : current,
    );
  }

  const stats = run.statistics as Record<string, number | string>;
  const decided = triage?.candidates.filter((c) => c.my_decision).length ?? 0;
  const shortlisted =
    triage?.candidates.filter((c) => c.my_decision?.verdict === "shortlisted").length ?? 0;

  return (
    <div className="space-y-6">
      <header className="border-b border-hairline pb-6">
        <nav className="flex flex-wrap items-center gap-2 text-2xs uppercase tracking-wider text-muted">
          <Link href="/programs" className="hover:text-accent">
            programs
          </Link>
          <span aria-hidden>/</span>
          {run.project_id ? (
            <Link href={`/programs/${run.project_id}`} className="hover:text-accent">
              {run.target_name ?? run.target_key}
            </Link>
          ) : (
            <span>{run.target_name ?? run.target_key}</span>
          )}
          <span aria-hidden>/</span>
          <span className="font-mono text-ink-2">{run.id.slice(0, 8)}</span>
        </nav>

        <div className="mt-3 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-semibold text-ink">
                {run.label ?? `Campaign ${run.id.slice(0, 8)}`}
              </h1>
              <StatusBadge status={run.status} stage={run.stage} />
              <EvidenceTag tier="computed" />
            </div>
            <p className="mt-2 text-2xs text-muted">
              {run.created_by_name ? `started by ${run.created_by_name} · ` : ""}
              {formatRelative(run.created_at)}
              {run.finished_at ? ` · finished ${formatRelative(run.finished_at)}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Chip label="generator" value={run.generator} />
            <Chip label="constraints" value={run.constraint_set} />
            <Chip label="seed" value={run.seed ?? "—"} />
            <Chip label="requested" value={run.n_requested} />
          </div>
        </div>
      </header>

      {inFlight && (
        <Panel
          title="Running"
          subtitle="The server owns this work: it continues whether or not this page is open."
        >
          <CampaignProgress run={run} />
        </Panel>
      )}

      {run.status === "failed" && (
        <Alert title="campaign failed">
          {run.error}
          <span className="mt-2 block text-2xs text-muted">
            Nothing was substituted for the component that failed. The campaign is kept with this
            reason attached so the attempt stays on the record.
          </span>
        </Alert>
      )}

      {run.status === "succeeded" && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <Stat
              label="Kept"
              value={`${run.n_passed} / ${run.n_candidates}`}
              context="Passed every hard constraint."
            />
            <Stat
              label="Validity"
              value={formatPercent(stats.validity_rate)}
              context="Chemically valid proposals. Duplicates count as valid — fine, just not new."
            />
            <Stat
              label="Uniqueness"
              value={formatPercent(stats.uniqueness_rate)}
              context={`${stats.n_duplicate ?? 0} duplicate${stats.n_duplicate === 1 ? "" : "s"} of the valid proposals.`}
            />
            <Stat
              label="Discarded"
              value={run.n_discarded}
              context="Never became candidates. Each keeps its reason."
            />
            <Stat
              label="You decided"
              value={`${decided} / ${triage?.candidates.length ?? 0}`}
              tone={shortlisted > 0 ? "good" : "neutral"}
              context={`${shortlisted} shortlisted so far.`}
            />
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,300px)_minmax(0,1fr)]">
            <div className="space-y-5">
              <Panel
                title="Your weights"
                subtitle="An ordering is a reading order, not a prediction. Move a weight and it recomputes; nothing is stored."
              >
                {triage ? (
                  <Weights
                    criteria={triage.criteria}
                    weights={weights}
                    degenerate={triage.degenerate_criteria}
                    busy={busy}
                    onChange={changeWeight}
                    onReset={resetWeights}
                  />
                ) : (
                  <p className="text-xs text-muted">No ordering yet.</p>
                )}

                <label className="mt-4 flex items-start gap-2 border-t border-hairline pt-3 text-2xs leading-relaxed text-ink-2">
                  <input
                    type="checkbox"
                    checked={!passedOnly}
                    onChange={togglePassedOnly}
                    className="mt-0.5 accent-[var(--accent)]"
                  />
                  <span>
                    Include candidates that failed a constraint
                    {triage && triage.n_excluded > 0 && (
                      <span className="mt-0.5 block text-muted">
                        {triage.n_excluded} currently excluded from this ordering.
                      </span>
                    )}
                  </span>
                </label>
              </Panel>

              {Object.keys(run.rejections).length > 0 && (
                <Panel
                  title="Why proposals were dropped"
                  subtitle="The most informative output of a generation run."
                >
                  <Rejections rejections={run.rejections} />
                </Panel>
              )}
            </div>

            <Panel
              title="Triage"
              subtitle="In your weighted order. Expand a row to audit the score; shortlist or reject with a reason."
              aside={
                triage && (
                  <span className="text-2xs text-muted">
                    {triage.candidates.length} shown
                  </span>
                )
              }
              bodyClassName="p-5 pt-3"
            >
              {error && (
                <div className="mb-3">
                  <Alert title="refused">{error}</Alert>
                </div>
              )}
              <motion.div animate={{ opacity: busy ? 0.55 : 1 }} transition={{ duration: 0.18 }}>
                <TriageTable
                  rows={triage?.candidates ?? []}
                  runId={run.id}
                  criterionLabels={criterionLabels}
                  weights={weights}
                  onDecision={applyDecision}
                />
              </motion.div>
              {triage && (
                <p className="mt-4 border-t border-hairline pt-3 text-2xs leading-relaxed text-muted">
                  {triage.basis}
                </p>
              )}
            </Panel>
          </div>

          {run.discards.length > 0 && (
            <Panel
              title="Discarded proposals"
              subtitle="Kept deliberately: these reasons say what the generator is doing wrong, which is how you decide whether to change generator or change constraints."
            >
              <div className="max-h-80 overflow-y-auto">
                <table className="w-full border-collapse text-xs">
                  <thead className="sticky top-0 bg-surface">
                    <tr className="border-b border-baseline text-left">
                      <th scope="col" className="py-2 pr-4 text-2xs uppercase tracking-wider text-muted">
                        Proposed
                      </th>
                      <th scope="col" className="py-2 pr-4 text-2xs uppercase tracking-wider text-muted">
                        Stage
                      </th>
                      <th scope="col" className="py-2 text-2xs uppercase tracking-wider text-muted">
                        Reason
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.discards.map((discard, index) => (
                      <tr
                        key={`${discard.proposed_smiles}-${index}`}
                        className="border-b border-grid last:border-0"
                      >
                        <td className="max-w-[20rem] break-all py-2 pr-4 font-mono text-2xs text-ink-2">
                          {discard.proposed_smiles}
                        </td>
                        <td className="py-2 pr-4 font-mono text-2xs text-muted">
                          {discard.stage}
                        </td>
                        <td className="py-2 text-2xs leading-relaxed text-ink-2">
                          {discard.duplicate_of ? (
                            <>
                              Duplicate of{" "}
                              <span className="font-mono text-muted">{discard.duplicate_of}</span>
                            </>
                          ) : (
                            <>
                              {discard.codes.length > 0 && (
                                <span className="font-mono text-state-warn">
                                  {discard.codes.join(", ")}
                                </span>
                              )}{" "}
                              {discard.detail}
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          <Panel title="Provenance">
            <dl className="grid gap-x-8 gap-y-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <Entry label="Evidence tier" value={run.evidence} />
              <Entry label="Diversity backend" value={backendOf(run)} />
              <Entry label="Biologix" value={String(manifestOf(run).biologix_version ?? "—")} />
              <Entry label="Git revision" value={String(manifestOf(run).git_revision ?? "—")} />
            </dl>
            <p className="mt-4 max-w-4xl border-t border-hairline pt-4 text-2xs leading-relaxed text-muted">
              {run.note}
            </p>
          </Panel>
        </>
      )}
    </div>
  );
}

function Rejections({ rejections }: { rejections: Record<string, number> }) {
  const entries = Object.entries(rejections);
  const max = Math.max(...entries.map(([, value]) => value));
  return (
    <ul className="space-y-2">
      {entries.map(([code, count]) => (
        <li key={code} className="text-2xs">
          <div className="flex items-baseline justify-between gap-3">
            <span className="truncate font-mono text-ink-2" title={code}>
              {code}
            </span>
            <span className="tabular shrink-0 text-ink">{count}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-grid">
            <motion.div
              className="h-full rounded-full"
              style={{ background: "var(--seq-500)" }}
              initial={{ width: 0 }}
              animate={{ width: `${(count / max) * 100}%` }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Entry({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-muted">{label}</dt>
      <dd className="mt-1 break-all font-mono text-2xs text-ink-2">{value}</dd>
    </div>
  );
}

function manifestOf(run: CampaignDetail): Record<string, unknown> {
  return run.manifest ?? {};
}

/** What actually computed the fingerprints — not what was requested. */
function backendOf(run: CampaignDetail): string {
  const backend = (run.diversity?.cohort_distance_backend ?? {}) as {
    actual?: string;
    requested?: string;
  };
  if (!backend.actual) return "—";
  return backend.requested && backend.requested !== backend.actual
    ? `${backend.actual} (${backend.requested} requested)`
    : backend.actual;
}
