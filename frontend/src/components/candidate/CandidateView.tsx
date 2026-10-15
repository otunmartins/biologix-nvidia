"use client";

import Link from "next/link";
import { useState } from "react";
import { AnalysisPanel } from "@/components/candidate/AnalysisPanel";
import { ChainSchematic } from "@/components/candidate/ChainSchematic";
import { DecisionControl } from "@/components/campaign/DecisionControl";
import { Chip, EvidenceTag, Panel, Stat } from "@/components/ui/Primitives";
import { VerdictBadge } from "@/components/ui/Status";
import { formatNumber, formatRelative } from "@/lib/format";
import type { CandidateDetail, DecisionRecord } from "@/lib/types";

/**
 * One candidate, in full.
 *
 * The ordering a reader saw elsewhere is deliberately absent here: a score is
 * relative to the cohort it was computed in, so repeating it on a single
 * candidate's page would invite it to be read as a property of the molecule.
 * What this page shows instead is what was computed, from what, and against
 * which thresholds.
 */
export function CandidateView({
  candidate,
  runId,
  unavailable,
}: {
  candidate: CandidateDetail;
  runId: string;
  unavailable: Record<string, string | null>;
}) {
  const [decision, setDecision] = useState<DecisionRecord | null>(candidate.my_decision);
  const others = candidate.decisions.filter((d) => d.id !== decision?.id);

  const descriptors = candidate.descriptors;
  const constraints = candidate.constraints ?? [];

  return (
    <div className="space-y-6">
      <header className="border-b border-hairline pb-6">
        <nav className="flex flex-wrap items-center gap-2 text-2xs uppercase tracking-wider text-muted">
          <Link href={`/campaigns/${runId}`} className="hover:text-accent">
            campaign {runId.slice(0, 8)}
          </Link>
          <span aria-hidden>/</span>
          <span className="font-mono text-ink-2">{candidate.candidate_id.slice(0, 8)}</span>
        </nav>

        <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-semibold text-ink">{candidate.display_name}</h1>
              <EvidenceTag tier="computed" />
              {candidate.passed ? (
                <span className="rounded border border-state-good/40 px-2 py-0.5 text-2xs uppercase tracking-wider text-state-good">
                  passed all hard constraints
                </span>
              ) : (
                <span className="rounded border border-state-crit/40 px-2 py-0.5 text-2xs uppercase tracking-wider text-state-crit">
                  failed {candidate.failures.length}
                </span>
              )}
            </div>
            <p className="mt-2 break-all font-mono text-2xs text-muted">
              {candidate.repeat_units.join("  ·  ")}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Chip label="architecture" value={candidate.architecture} />
            <Chip label="end group" value={candidate.end_group} />
            <Chip label="dp" value={candidate.degree_of_polymerisation} />
            {candidate.generator && <Chip label="from" value={candidate.generator} />}
          </div>
        </div>
      </header>

      <ChainSchematic
        repeatUnits={candidate.repeat_units}
        dp={candidate.degree_of_polymerisation}
        endGroup={candidate.end_group}
        architecture={candidate.architecture}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Oligomer MW"
          value={formatNumber(descriptors.oligomer_mw ?? descriptors.mol_weight)}
          context={`Of the DP-${candidate.screening_dp ?? "?"} capped oligomer, not of a polydisperse polymer.`}
        />
        <Stat label="TPSA" value={formatNumber(descriptors.tpsa)} context="Topological polar surface area." />
        <Stat
          label="Rotatable bonds"
          value={formatNumber(descriptors.rotatable_bonds, 0)}
          context="Backbone flexibility, and the reason docking gets harder."
        />
        <Stat
          label="Cohort distance"
          value={
            candidate.cohort_distance === null
              ? "—"
              : candidate.cohort_distance.toFixed(3)
          }
          context="Mean Tanimoto distance from the rest of its campaign. Not comparable across campaigns."
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel
          title="Your decision"
          subtitle="A verdict needs a reason. The reason is the only part of a triage call that the numbers cannot give back later."
        >
          <DecisionControl
            runId={runId}
            candidateId={candidate.candidate_id}
            decision={decision}
            weights={decision?.weights_at_decision ?? {}}
            orderScore={decision?.order_score_at_decision ?? null}
            onChange={setDecision}
          />

          {others.length > 0 && (
            <div className="mt-5 border-t border-hairline pt-4">
              <h3 className="text-2xs uppercase tracking-wider text-muted">
                What others decided
              </h3>
              <ul className="mt-2.5 space-y-3">
                {others.map((other) => (
                  <li key={other.id}>
                    <div className="flex items-center gap-2">
                      <VerdictBadge verdict={other.verdict} />
                      <span className="text-2xs text-muted">
                        {formatRelative(other.updated_at ?? other.created_at)}
                      </span>
                    </div>
                    <p className="mt-1.5 text-2xs leading-relaxed text-ink-2">
                      “{other.rationale}”
                    </p>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-2xs leading-relaxed text-muted">
                Disagreement is kept rather than resolved: two people may hold opposite verdicts,
                and the shortlist shows whose is whose.
              </p>
            </div>
          )}
        </Panel>

        <Panel
          title="The oligomer these numbers describe"
          subtitle="Descriptors are computed from an explicit capped oligomer, so they can be interpreted as what they are."
        >
          <dl className="space-y-3">
            <div>
              <dt className="text-2xs uppercase tracking-wider text-muted">Oligomer SMILES</dt>
              <dd className="mt-1 break-all font-mono text-2xs text-ink-2">
                {candidate.oligomer_smiles ?? "—"}
              </dd>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <dt className="text-2xs uppercase tracking-wider text-muted">Screening DP</dt>
                <dd className="tabular mt-1 font-mono text-2xs text-ink-2">
                  {candidate.screening_dp ?? "—"}
                </dd>
              </div>
              <div>
                <dt className="text-2xs uppercase tracking-wider text-muted">Families</dt>
                <dd className="mt-1 font-mono text-2xs text-ink-2">
                  {candidate.families.length ? candidate.families.join(", ") : "—"}
                </dd>
              </div>
            </div>
            {candidate.descriptor_error && (
              <div>
                <dt className="text-2xs uppercase tracking-wider text-state-crit">
                  Descriptor error
                </dt>
                <dd className="mt-1 text-2xs leading-relaxed text-ink-2">
                  {candidate.descriptor_error}
                </dd>
              </div>
            )}
          </dl>

          {Object.keys(descriptors).length > 0 && (
            <table className="mt-5 w-full border-collapse text-2xs">
              <caption className="sr-only">Every descriptor computed for this oligomer</caption>
              <tbody className="tabular">
                {Object.entries(descriptors).map(([key, value]) => (
                  <tr key={key} className="border-b border-grid/60 last:border-0">
                    <td className="py-1.5 pr-4 font-mono text-muted">{key}</td>
                    <td className="py-1.5 text-right text-ink-2">
                      {typeof value === "number" ? formatNumber(value, 3) : String(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      {(candidate.failures.length > 0 ||
        candidate.warnings.length > 0 ||
        constraints.length > 0) && (
        <Panel
          title="Constraints it was judged against"
          subtitle="Editable heuristics with stated rationales, not regulatory limits. A failure here is a result, not a defect."
        >
          {candidate.failures.length > 0 && (
            <ul className="mb-4 space-y-1.5">
              {candidate.failures.map((failure) => (
                <li key={failure} className="text-2xs leading-relaxed text-state-crit">
                  ✕ {failure}
                </li>
              ))}
            </ul>
          )}
          {candidate.warnings.length > 0 && (
            <ul className="mb-4 space-y-1.5">
              {candidate.warnings.map((warning) => (
                <li key={warning} className="text-2xs leading-relaxed text-state-warn">
                  ! {warning}
                </li>
              ))}
            </ul>
          )}
          {constraints.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] border-collapse text-2xs">
                <thead>
                  <tr className="border-b border-baseline text-left text-muted">
                    <th scope="col" className="py-1.5 pr-4 font-medium uppercase tracking-wider">
                      Descriptor
                    </th>
                    <th scope="col" className="py-1.5 pr-4 font-medium uppercase tracking-wider">
                      Band
                    </th>
                    <th scope="col" className="py-1.5 pr-4 font-medium uppercase tracking-wider">
                      This candidate
                    </th>
                    <th scope="col" className="py-1.5 font-medium uppercase tracking-wider">
                      Why the threshold exists
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {constraints.map((constraint) => {
                    const value = descriptors[constraint.descriptor];
                    return (
                      <tr key={constraint.descriptor} className="border-b border-grid/60 last:border-0">
                        <td className="py-2 pr-4 font-mono text-ink-2">
                          {constraint.descriptor}
                          {constraint.hard === false && (
                            <span className="ml-1.5 text-muted">(soft)</span>
                          )}
                        </td>
                        <td className="tabular py-2 pr-4 text-muted">
                          {constraint.minimum ?? "−∞"} … {constraint.maximum ?? "∞"}
                        </td>
                        <td className="tabular py-2 pr-4 text-ink">
                          {typeof value === "number" ? formatNumber(value, 2) : "—"}
                        </td>
                        <td className="py-2 leading-relaxed text-muted">
                          {constraint.rationale ?? "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {candidate.monomer_flags.length > 0 && (
        <Panel
          title="Residual-monomer flags"
          subtitle="Scoped to the monomer, never to the polymer. This is not a safety assessment."
        >
          <ul className="space-y-3">
            {candidate.monomer_flags.map((flag) => (
              <li key={flag.monomer_class} className="rounded-lg border border-hairline bg-surface-2 p-3.5">
                <p className="text-xs text-ink-2">
                  <span className="font-mono text-state-warn">{flag.monomer_class}</span> —{" "}
                  {flag.concern}
                </p>
                <p className="mt-1.5 text-2xs leading-relaxed text-muted">
                  Scope: {flag.scope}. {flag.does_not_imply}
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        title="NVIDIA analyses"
        subtitle="The expensive half of the platform: DiffDock for geometry, Boltz-2 for a complex and an affinity figure. Both run as tracked jobs on the server."
      >
        <AnalysisPanel
          runId={runId}
          candidateId={candidate.candidate_id}
          initialJobs={candidate.jobs}
          dpSuggestion={candidate.degree_of_polymerisation}
          unavailable={unavailable}
        />
      </Panel>
    </div>
  );
}
