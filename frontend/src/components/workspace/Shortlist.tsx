import Link from "next/link";
import { Empty, EvidenceTag } from "@/components/ui/Primitives";
import { formatNumber, formatRelative } from "@/lib/format";
import type { CandidateRow, DecisionRecord } from "@/lib/types";

/**
 * The program's shortlist: the one output a program actually has.
 *
 * Each entry leads with the reason the person gave, not the numbers. The
 * numbers are recoverable from the campaign at any time; the reasoning is not,
 * which is why it is stored and why it is shown first.
 */
export function Shortlist({
  entries,
}: {
  entries: { decision: DecisionRecord; candidate: CandidateRow }[];
}) {
  if (!entries.length) {
    return (
      <Empty title="Nothing shortlisted yet.">
        Open a finished campaign, order the candidates by the criteria you care about, and
        shortlist the ones worth laboratory time. A shortlist entry always carries a reason.
      </Empty>
    );
  }

  return (
    <ul className="space-y-3">
      {entries.map(({ decision, candidate }) => (
        <li
          key={`${decision.run_id}-${decision.candidate_id}`}
          className="rounded-xl border border-hairline bg-surface-2 p-4"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <Link
                href={`/campaigns/${decision.run_id}/candidates/${decision.candidate_id}`}
                className="text-xs text-ink underline-offset-2 hover:text-accent hover:underline"
              >
                {candidate.display_name}
              </Link>
              <p className="mt-1 break-all font-mono text-2xs text-muted">
                {candidate.repeat_units.join(" · ")}
              </p>
            </div>
            <EvidenceTag tier="computed" />
          </div>

          <blockquote className="mt-3 border-l-2 border-state-good/50 pl-3 text-xs leading-relaxed text-ink-2">
            {decision.rationale}
          </blockquote>

          <dl className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-2xs text-muted">
            <span>
              <dt className="inline uppercase tracking-wider">DP</dt>{" "}
              <dd className="tabular inline text-ink-2">{candidate.degree_of_polymerisation}</dd>
            </span>
            <span>
              <dt className="inline uppercase tracking-wider">TPSA</dt>{" "}
              <dd className="tabular inline text-ink-2">
                {formatNumber(candidate.descriptors.tpsa)}
              </dd>
            </span>
            {decision.order_score_at_decision !== null && (
              <span title="The order score the decider was looking at, under their weights at the time">
                <dt className="inline uppercase tracking-wider">score then</dt>{" "}
                <dd className="tabular inline text-ink-2">
                  {decision.order_score_at_decision.toFixed(3)}
                </dd>
              </span>
            )}
            <span>
              <dt className="inline uppercase tracking-wider">decided</dt>{" "}
              <dd className="inline text-ink-2">
                {formatRelative(decision.updated_at ?? decision.created_at)}
              </dd>
            </span>
          </dl>

          {decision.weights_at_decision && (
            <p className="mt-2 text-2xs text-muted">
              Under weights{" "}
              <span className="font-mono text-ink-2">
                {Object.entries(decision.weights_at_decision)
                  .filter(([, value]) => value > 0)
                  .map(([key, value]) => `${key} ${value.toFixed(2)}`)
                  .join(" · ")}
              </span>
              . Those weights are kept so the call can be read in the ordering that produced it.
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
