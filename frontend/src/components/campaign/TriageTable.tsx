"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { Fragment, useState } from "react";
import { DecisionControl } from "@/components/campaign/DecisionControl";
import { Empty } from "@/components/ui/Primitives";
import { JobChip } from "@/components/ui/Status";
import { formatNumber } from "@/lib/format";
import type { Contribution, DecisionRecord, TriageRow } from "@/lib/types";

/**
 * The triage table.
 *
 * The score column carries a stacked bar of the row's weighted contributions,
 * so an ordering can be audited by eye: a row sits where it does because of
 * segments a reader can see and name. The segments are a single hue stepped
 * by criterion — they are parts of one quantity, not categories, so a
 * sequential ramp is the honest encoding. Expanding a row repeats every
 * figure as text, which is the channel doing the real work.
 */
export function TriageTable({
  rows,
  runId,
  criterionLabels,
  weights,
  onDecision,
}: {
  rows: TriageRow[];
  runId: string;
  criterionLabels: Record<string, string>;
  weights: Record<string, number>;
  onDecision: (candidateId: string, decision: DecisionRecord | null) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const top = rows[0]?.order_score ?? 1;

  if (!rows.length) {
    return (
      <Empty title="No candidate here can be ranked.">
        Ranking needs descriptors, and every candidate in this view either failed to produce them
        or was filtered out. The campaign record below shows what was screened and why each one
        was dropped.
      </Empty>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[980px] border-collapse text-xs">
        <caption className="sr-only">
          Candidates in your weighted order. The order score is a sorting key, not a prediction.
        </caption>
        <thead>
          <tr className="border-b border-baseline text-left">
            <th scope="col" className={head}>
              #
            </th>
            <th scope="col" className={head}>
              Candidate
            </th>
            <th scope="col" className={head}>
              Order score
            </th>
            <th scope="col" className={head}>
              DP
            </th>
            <th scope="col" className={head}>
              MW
            </th>
            <th scope="col" className={head}>
              TPSA
            </th>
            <th scope="col" className={head}>
              logP
            </th>
            <th scope="col" className={head}>
              Rot. bonds
            </th>
            <th scope="col" className={head}>
              Distance
            </th>
            <th scope="col" className={head}>
              Your call
            </th>
            <th scope="col" className={head}>
              <span className="sr-only">Detail</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const expanded = open === row.candidate_id;
            return (
              <Fragment key={row.candidate_id}>
                <motion.tr
                  layout="position"
                  transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                  className={`border-b border-grid align-top last:border-0 ${
                    row.my_decision?.verdict === "rejected" ? "opacity-55" : ""
                  }`}
                >
                  <td className="py-3 pr-3 tabular text-2xs text-muted">{index + 1}</td>
                  <td className="py-3 pr-4">
                    <Link
                      href={`/campaigns/${runId}/candidates/${row.candidate_id}`}
                      className="text-ink underline-offset-2 hover:text-accent hover:underline"
                    >
                      {row.display_name}
                    </Link>
                    <p className="mt-0.5 break-all font-mono text-2xs text-muted">
                      {row.repeat_units.join(" · ")}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {!row.passed && (
                        <span className="rounded border border-state-crit/40 px-1.5 py-0.5 text-2xs uppercase tracking-wider text-state-crit">
                          failed {row.failures.length}
                        </span>
                      )}
                      {row.warnings.length > 0 && (
                        <span className="rounded border border-state-warn/40 px-1.5 py-0.5 text-2xs uppercase tracking-wider text-state-warn">
                          {row.warnings.length} warning
                        </span>
                      )}
                      {row.monomer_flags.length > 0 && (
                        <span
                          title="Residual free monomer only; monomer toxicology does not transfer to the polymer"
                          className="rounded border border-hairline px-1.5 py-0.5 text-2xs uppercase tracking-wider text-muted"
                        >
                          monomer flag
                        </span>
                      )}
                      {row.jobs.map((job) => (
                        <JobChip key={job.id} job={job} />
                      ))}
                    </div>
                  </td>
                  <td className="py-3 pr-4">
                    <span className="tabular font-mono text-ink">
                      {row.order_score.toFixed(3)}
                    </span>
                    <ContributionBar
                      contributions={row.contributions}
                      scale={top}
                      labels={criterionLabels}
                    />
                  </td>
                  <td className="py-3 pr-4 tabular text-ink-2">
                    {row.degree_of_polymerisation}
                  </td>
                  <td className="py-3 pr-4 tabular text-ink-2">
                    {formatNumber(row.descriptors.oligomer_mw ?? row.descriptors.mol_weight)}
                  </td>
                  <td className="py-3 pr-4 tabular text-ink-2">
                    {formatNumber(row.descriptors.tpsa)}
                  </td>
                  <td className="py-3 pr-4 tabular text-ink-2">
                    {formatNumber(row.descriptors.logp ?? row.descriptors.clogp, 2)}
                  </td>
                  <td className="py-3 pr-4 tabular text-ink-2">
                    {formatNumber(row.descriptors.rotatable_bonds, 0)}
                  </td>
                  <td className="py-3 pr-4 tabular text-ink-2">
                    {row.cohort_distance === null ? (
                      <span className="text-muted" title="no cohort to compare against">
                        —
                      </span>
                    ) : (
                      row.cohort_distance.toFixed(3)
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <DecisionControl
                      runId={runId}
                      candidateId={row.candidate_id}
                      decision={row.my_decision}
                      weights={weights}
                      orderScore={row.order_score}
                      onChange={(decision) => onDecision(row.candidate_id, decision)}
                      compact
                    />
                  </td>
                  <td className="py-3">
                    <button
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => setOpen(expanded ? null : row.candidate_id)}
                      className="rounded border border-hairline px-2 py-0.5 text-2xs uppercase tracking-wider text-muted transition-colors hover:border-accent hover:text-ink"
                    >
                      {expanded ? "hide" : "why"}
                    </button>
                  </td>
                </motion.tr>

                <AnimatePresence>
                  {expanded && (
                    <tr className="border-b border-grid">
                      <td colSpan={11} className="bg-surface-2/60 p-0">
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                          className="overflow-hidden"
                        >
                          <div className="px-4 py-4">
                            <RowDetail row={row} labels={criterionLabels} />
                          </div>
                        </motion.div>
                      </td>
                    </tr>
                  )}
                </AnimatePresence>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const head = "py-2 pr-4 text-2xs font-medium uppercase tracking-wider text-muted";

/** One hue, stepped by criterion: contributions are parts of one quantity. */
const RAMP = ["var(--seq-400)", "var(--seq-500)", "var(--seq-600)", "var(--accent)"];

function ContributionBar({
  contributions,
  scale,
  labels,
}: {
  contributions: Contribution[];
  scale: number;
  labels: Record<string, string>;
}) {
  const live = contributions.filter((c) => c.contribution > 0);
  if (!live.length) return null;
  return (
    <div
      className="mt-1.5 flex h-1.5 w-36 overflow-hidden rounded-full bg-grid"
      role="img"
      aria-label={live
        .map((c) => `${labels[c.criterion] ?? c.criterion} ${c.contribution.toFixed(3)}`)
        .join(", ")}
    >
      {live.map((c, index) => (
        <motion.span
          key={c.criterion}
          layout
          title={`${labels[c.criterion] ?? c.criterion}: ${c.contribution.toFixed(3)}`}
          animate={{ width: `${(c.contribution / Math.max(scale, 1e-9)) * 100}%` }}
          transition={{ duration: 0.35, ease: "easeOut" }}
          style={{ background: RAMP[index % RAMP.length] }}
        />
      ))}
    </div>
  );
}

function RowDetail({ row, labels }: { row: TriageRow; labels: Record<string, string> }) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <h3 className="text-2xs uppercase tracking-wider text-muted">
          Why this row sits where it does
        </h3>
        <table className="mt-2 w-full border-collapse text-2xs">
          <thead>
            <tr className="border-b border-grid text-left text-muted">
              <th scope="col" className="py-1 pr-3 font-medium">
                Criterion
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Value
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Normalised
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                Weight
              </th>
              <th scope="col" className="py-1 font-medium">
                Contribution
              </th>
            </tr>
          </thead>
          <tbody className="tabular">
            {row.contributions.map((c) => (
              <tr key={c.criterion} className="border-b border-grid/60 last:border-0">
                <td className="py-1 pr-3 text-ink-2">{labels[c.criterion] ?? c.criterion}</td>
                <td className="py-1 pr-3 text-ink-2">{c.value === null ? "—" : c.value}</td>
                <td className="py-1 pr-3 text-ink-2">
                  {c.normalised === null ? "—" : c.normalised.toFixed(3)}
                  {c.degenerate && (
                    <span
                      title="Every candidate in this cohort scores the same here, so the criterion cannot separate them"
                      className="ml-1 text-state-warn"
                    >
                      degenerate
                    </span>
                  )}
                </td>
                <td className="py-1 pr-3 text-ink-2">{c.weight.toFixed(3)}</td>
                <td className="py-1 text-ink">{c.contribution.toFixed(4)}</td>
              </tr>
            ))}
            <tr className="border-t border-baseline">
              <td className="py-1 pr-3 text-muted" colSpan={4}>
                Order score
              </td>
              <td className="py-1 font-mono text-ink">{row.order_score.toFixed(4)}</td>
            </tr>
          </tbody>
        </table>
        {row.missing_descriptors.length > 0 && (
          <p className="mt-2 text-2xs leading-relaxed text-state-warn">
            Missing descriptors, which contribute nothing rather than a guess:{" "}
            {row.missing_descriptors.join(", ")}.
          </p>
        )}
      </div>

      <div className="space-y-4">
        <div>
          <h3 className="text-2xs uppercase tracking-wider text-muted">
            The oligomer these numbers describe
          </h3>
          <p className="mt-1.5 break-all font-mono text-2xs text-ink-2">
            {row.oligomer_smiles ?? "—"}
          </p>
          <p className="mt-1.5 text-2xs leading-relaxed text-muted">
            DP {row.screening_dp ?? "—"} capped with {row.end_group}; {row.architecture}. These
            descriptors are properties of this oligomer, not of a polydisperse polymer.
          </p>
        </div>

        {row.failures.length > 0 && (
          <div>
            <h3 className="text-2xs uppercase tracking-wider text-muted">Constraints failed</h3>
            <ul className="mt-1.5 space-y-1">
              {row.failures.map((failure) => (
                <li key={failure} className="text-2xs leading-relaxed text-state-crit">
                  {failure}
                </li>
              ))}
            </ul>
          </div>
        )}

        {row.warnings.length > 0 && (
          <div>
            <h3 className="text-2xs uppercase tracking-wider text-muted">Soft warnings</h3>
            <ul className="mt-1.5 space-y-1">
              {row.warnings.map((warning) => (
                <li key={warning} className="text-2xs leading-relaxed text-state-warn">
                  {warning}
                </li>
              ))}
            </ul>
          </div>
        )}

        {row.monomer_flags.map((flag) => (
          <div key={flag.monomer_class}>
            <h3 className="text-2xs uppercase tracking-wider text-muted">
              Residual-monomer flag
            </h3>
            <p className="mt-1.5 text-2xs leading-relaxed text-ink-2">
              <span className="font-mono text-state-warn">{flag.monomer_class}</span> —{" "}
              {flag.concern}
            </p>
            <p className="mt-1 text-2xs leading-relaxed text-muted">
              Scope: {flag.scope}. {flag.does_not_imply}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
